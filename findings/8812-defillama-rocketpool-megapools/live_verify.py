#!/usr/bin/env python3
"""Re-read the on-chain facts of DefiLlama/dimension-adapters#8812 at fixed blocks.

Usage:
    ETH_ARCHIVE_RPC=<archive endpoint> [ETHERSCAN_API_KEY=<key>] python3 live_verify.py --check
    ETH_ARCHIVE_RPC=<archive endpoint> [ETHERSCAN_API_KEY=<key>] python3 live_verify.py --write

Calls and block headers go to ETH_ARCHIVE_RPC. Logs go to the Etherscan API v2 when
ETHERSCAN_API_KEY is set, otherwise to ETH_ARCHIVE_RPC as eth_getLogs (ranges are halved
whenever the endpoint refuses them). GITHUB_TOKEN, if set, is sent to api.github.com only,
to lift its 60-calls-an-hour limit. Only on-chain fields are kept, so both readers give the
same snapshot. Endpoints and keys are never written to disk.

--check compares a fresh read with data/live_snapshot.json:
    exit 0  identical
    exit 1  a historical read differs (blocks, calls or logs)
    exit 2  no usable endpoint, the endpoint does not serve history, or a read failed
    exit 3  only the public sources differ (GitHub)
    exit 4  a public source could not be read (the historical check still ran)
--write replaces the snapshot, and refuses to write a partial one.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

CASE = Path(__file__).resolve().parent
sys.path.insert(0, str(CASE.parent.parent / "lib"))
import evm  # noqa: E402

SNAPSHOT = CASE / "data" / "live_snapshot.json"

ROCKET_STORAGE = "0x1d8f8f00cfa6758d7be78336684788fb0ee0fa46"
HEAD = 25772201        # head read by the original run; the issue's calls are stated at this block
PAY = 25772216         # the issue's log totals and vault identity are stated up to this block
ACTIVATION = 24479994  # the issue: RocketStorage began serving the Saturn 1 contracts here
SCAN_FROM = 24416500   # start of the original run's RewardsDistributed scan
CONTROL_MINIPOOL_INDEXES = (0, 21158, 42316)
CONTRACTS = ("rocketTokenRETH", "rocketMinipoolManager", "rocketDAOProtocolSettingsNetwork",
             "rocketNetworkRevenues", "rocketVault", "rocketRewardsPool")
SATURN_CONTRACTS = ("rocketMegapoolFactory", "rocketMegapoolManager")

T_RETH_DEPOSIT = evm.topic("EtherDeposited(address,uint256,uint256)")
T_REWARDS = evm.topic("RewardsDistributed(uint256,uint256,uint256,uint256,uint256)")
T_VAULT_DEPOSIT = evm.topic("EtherDeposited(string,uint256,uint256)")
T_VAULT_WITHDRAW = evm.topic("EtherWithdrawn(string,uint256,uint256)")
REWARDS_POOL_TOPIC = evm.hex32(evm.keccak256(b"rocketRewardsPool"))

GITHUB = "DefiLlama/dimension-adapters"
SUPPLY_SIDE_TEXT = "Total ETH staking rewards are distributed to rETH stakers and minipool depositors"
ADAPTER_PATH = "fees/rocketpool.ts"


class Reads:
    """Collects every call, block header and log query in one snapshot-shaped dict."""

    def __init__(self, rpc, logs):
        self.rpc, self.reader, self.blocks, self.calls, self.logs = rpc, logs, {}, [], []

    def block(self, n):
        if str(n) not in self.blocks:
            self.blocks[str(n)] = self.rpc.block(n)

    def call(self, label, to, data, block):
        self.block(block)
        out = self.rpc.call(to, data, block)
        self.calls.append(dict({"label": label, "block": block, "to": to.lower(), "data": data}, **out))
        return out

    def word(self, label, to, data, block):
        out = self.call(label, to, data, block)
        if out.get("reverted") or len(out["result"]) < 66:
            raise evm.ReaderError(f"{label}: no value")
        return out["result"]

    def query(self, label, address, topics, lo, hi):
        self.block(hi)
        rows = evm.sort_logs(self.reader.logs(address, topics, lo, hi))
        self.logs.append(evm.pack(label, address, topics, lo, hi, rows))
        return rows


def resolve(r, name, block):
    key = evm.storage_key("contract.address", name.encode())
    return evm.address_of(r.word(f"getAddress(contract.address{name})@{block}", ROCKET_STORAGE,
                                 evm.calldata("getAddress(bytes32)", key[2:]), block))


def read_chain(r):
    if r.rpc.request("eth_chainId", []) != "0x1":
        raise evm.ReaderError("not Ethereum mainnet")
    addr = {name: resolve(r, name, HEAD) for name in CONTRACTS}
    for name in ("rocketVault", "rocketRewardsPool"):
        if resolve(r, name, PAY) != addr[name]:
            raise evm.ReaderError(f"{name} moved between {HEAD} and {PAY}")
    for name in SATURN_CONTRACTS:
        resolve(r, name, ACTIVATION - 1)
        resolve(r, name, ACTIVATION)

    # Seed set: every emitter of RewardsDistributed, then the protocol's own megapool flag.
    rewards = r.query("RewardsDistributed, any emitter", None, [T_REWARDS], SCAN_FROM, PAY)
    for emitter in sorted({l["address"] for l in rewards}):
        key = evm.storage_key("megapool.exists", bytes.fromhex(emitter[2:]))
        r.word(f"megapool.exists({emitter})", ROCKET_STORAGE, evm.calldata("getBool(bytes32)", key[2:]), HEAD)
    flagged = sorted(c["label"][len("megapool.exists("):-1] for c in r.calls
                     if c["label"].startswith("megapool.exists(") and int(c["result"], 16) == 1)

    for mp in flagged:
        for fn in ("getNodeDepositBalance()", "getNodeFee()"):
            r.call(f"{fn}@{mp}", mp, evm.selector(fn), HEAD)
        r.word(f"getMinipoolExists({mp})", addr["rocketMinipoolManager"],
               evm.calldata("getMinipoolExists(address)", evm.word_address(mp)[2:]), HEAD)
        r.query(f"rETH EtherDeposited from {mp}", addr["rocketTokenRETH"], [T_RETH_DEPOSIT, evm.word_address(mp)], 0, PAY)

    r.word("getMinipoolCount()", addr["rocketMinipoolManager"], evm.selector("getMinipoolCount()"), HEAD)
    for i in CONTROL_MINIPOOL_INDEXES:
        mp = evm.address_of(r.word(f"getMinipoolAt({i})", addr["rocketMinipoolManager"],
                                   evm.calldata("getMinipoolAt(uint256)", evm.enc_uint(i)), HEAD))
        for fn in ("getNodeDepositBalance()", "getNodeFee()"):
            r.word(f"{fn}@minipool {i}", mp, evm.selector(fn), HEAD)
        if i == CONTROL_MINIPOOL_INDEXES[-1]:
            r.query(f"rETH EtherDeposited from minipool {i}", addr["rocketTokenRETH"],
                    [T_RETH_DEPOSIT, evm.word_address(mp)], 0, PAY)

    settings = addr["rocketDAOProtocolSettingsNetwork"]
    for fn in ("getVoterShare()", "getNodeShare()", "getProtocolDAOShare()"):
        r.word(fn, settings, evm.selector(fn), HEAD)
    r.word("getCurrentVoterShare()", addr["rocketNetworkRevenues"], evm.selector("getCurrentVoterShare()"), HEAD)

    r.word("rocketVault.balanceOf(rocketRewardsPool)", addr["rocketVault"],
           evm.calldata("balanceOf(string)", evm.enc_string("rocketRewardsPool")), PAY)
    r.word("getPendingVoterShare()", addr["rocketRewardsPool"], evm.selector("getPendingVoterShare()"), PAY)
    r.query("rocketVault EtherDeposited by rocketRewardsPool", addr["rocketVault"],
            [T_VAULT_DEPOSIT, REWARDS_POOL_TOPIC], 0, PAY)
    r.query("rocketVault EtherWithdrawn by rocketRewardsPool", addr["rocketVault"],
            [T_VAULT_WITHDRAW, REWARDS_POOL_TOPIC], 0, PAY)
    return {"blocks": r.blocks, "calls": r.calls, "logs": r.logs}


def archive_checks(rpc):
    """Fails unless the endpoint serves state at old blocks rather than silently answering `latest`."""
    key = evm.storage_key("contract.address", b"rocketMegapoolFactory")
    data = evm.calldata("getAddress(bytes32)", key[2:])
    before = rpc.call(ROCKET_STORAGE, data, ACTIVATION - 100_000)
    now = rpc.call(ROCKET_STORAGE, data, HEAD)
    genesis = rpc.request("eth_getCode", [ROCKET_STORAGE, hex(1)])
    ok = genesis in ("0x", "0x0") and before.get("result") != now.get("result")
    return {"code_at_block_1_empty": genesis in ("0x", "0x0"),
            "factory_address_differs_before_and_after_activation": before.get("result") != now.get("result"),
            "pass": ok}


def http(url):
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/vnd.github+json"}
    if url.startswith("https://api.github.com/") and os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]  # optional: 60 unauthenticated calls an hour
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60).read()


def gh(path):
    return json.loads(http(f"https://api.github.com/repos/{GITHUB}/{path}"))


def methodology_labels(text):
    """Top-level keys of the adapter's `methodology` object, in order."""
    m = re.search(r"\bmethodology\s*[:=]\s*\{(.*?)\n\s*\}", text, re.S)
    return re.findall(r"^\s*(\w+)\s*:", m.group(1), re.M) if m else None


def adapter_facts(src):
    text = src.decode()
    return {
        "sha256": hashlib.sha256(src).hexdigest(),
        "calls_getNodeDepositBalance": "getNodeDepositBalance" in text,
        "calls_getNodeFee": "getNodeFee" in text,
        "permit_failure": bool(re.search(r"permitFailure:\s*true", text)),
        "skips_when_either_is_null": bool(re.search(
            r"if\s*\(\s*nodeBalance\s*===\s*null\s*\|\|\s*nodeFeeRate\s*===\s*null\s*\)\s*\{\s*continue", text)),
        "subtracts_24_and_16_eth": "24e18" in text and "16e18" in text,
        "supply_side_text_pre_saturn": SUPPLY_SIDE_TEXT in text,
        "revenue_text_no_cut": "Rocket Pool protocol doesn't take any fees or rewards cut." in text,
        "methodology_labels": methodology_labels(text),
        "has_user_fees_label": bool(re.search(r"\bUserFees\b", text)),
        "has_protocol_revenue_label": bool(re.search(r"\bProtocolRevenue\b", text)),
        "reads_megapool_exists_flag": "megapool.exists" in text,
        "reads_rewards_distributed_of_megapools": bool(re.search(r"targets:\s*megapoolAddresses", text))
                                                  and "RewardsDistributed" in text,
    }


def public_state():
    pr = gh("pulls/8833")
    merge = pr["merge_commit_sha"]
    parent = gh(f"commits/{merge}")["parents"][0]["sha"]
    body = pr.get("body") or ""
    issue = gh("issues/8812")
    out = {
        "pr_8833": {"merged_at": pr["merged_at"], "merge_commit_sha": merge, "parent_sha": parent,
                    "body_closes_8812": bool(re.search(r"(close[sd]?|fix(es|ed)?)\s+#8812\b", body, re.I)),
                    "body_cites_78_23_eth": "78.23" in body},
        "issue_8812": {"state": issue["state"], "state_reason": issue.get("state_reason"), "closed_at": issue["closed_at"]},
        "adapter_before_fix": adapter_facts(http(f"https://raw.githubusercontent.com/{GITHUB}/{parent}/{ADAPTER_PATH}")),
        "adapter_after_fix": adapter_facts(http(f"https://raw.githubusercontent.com/{GITHUB}/{merge}/{ADAPTER_PATH}")),
    }
    for n in (2765, 3519):
        p = gh(f"pulls/{n}")
        files = gh(f"pulls/{n}/files?per_page=100")
        f = next((x for x in files if x["filename"] == ADAPTER_PATH), None)
        patch = (f or {}).get("patch") or ""
        out[f"pr_{n}"] = {"merged_at": p["merged_at"],
                          "adapter_status": f["status"] if f else None,
                          "adds_getNodeFee": any(l.startswith("+") and "getNodeFee" in l for l in patch.splitlines()),
                          "adds_zero_revenue": any(re.fullmatch(r"\+\s*dailyRevenue:\s*0,?", l.strip())
                                                   for l in patch.splitlines())}
    return out


def readers():
    url = os.environ.get("ETH_ARCHIVE_RPC")
    if not url:
        return None, None
    rpc = evm.Rpc(url)
    key = os.environ.get("ETHERSCAN_API_KEY")
    return rpc, (evm.Etherscan(key) if key else rpc)


def diff(a, b, path=""):
    if type(a) is not type(b):
        return [path or "/"]
    if isinstance(a, dict):
        return [p for k in sorted(set(a) | set(b)) for p in (diff(a.get(k), b.get(k), f"{path}/{k}") if k in a and k in b else [f"{path}/{k}"])]
    if isinstance(a, list):
        if len(a) != len(b):
            return [f"{path} (length {len(a)} vs {len(b)})"]
        return [p for i, (x, y) in enumerate(zip(a, b)) for p in diff(x, y, f"{path}[{i}]")]
    return [] if a == b else [path]


def main(argv=None, rpc=None, logs=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    if rpc is None:
        rpc, logs = readers()
    if rpc is None:
        print("set ETH_ARCHIVE_RPC to an Ethereum archive endpoint", file=sys.stderr)
        return 2
    try:
        checks = archive_checks(rpc)
        if not checks["pass"]:
            print(f"endpoint does not serve history: {checks}", file=sys.stderr)
            return 2
        fresh = read_chain(Reads(rpc, logs))
    except Exception as e:  # any failure while reading means nothing was compared: never exit 1 for it
        print(f"a read failed, nothing compared: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    try:
        public, public_error = public_state(), None
    except Exception as e:  # network or API failure; the historical part still counts
        public, public_error = None, f"{type(e).__name__}"
    reader = "Etherscan API v2 getLogs" if isinstance(logs, evm.Etherscan) else "archive JSON-RPC eth_getLogs"
    if args.write:
        if public is None:
            print(f"public sources unreadable ({public_error}); snapshot not written", file=sys.stderr)
            return 4
        snap = {"case": "DefiLlama/dimension-adapters#8812",
                "checked_at_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "readers": {"calls": "archive JSON-RPC (endpoint not recorded)", "logs": reader},
                "archive_checks": checks, **fresh, "public": public}
        SNAPSHOT.write_text(json.dumps(snap, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {SNAPSHOT.name}: {len(fresh['calls'])} calls, {sum(len(q['rows']) for q in fresh['logs'])} logs")
        return 0
    saved = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    hist = diff({k: saved[k] for k in ("blocks", "calls", "logs")}, fresh)
    if hist:
        print("historical reads differ:\n  " + "\n  ".join(hist[:20]))
        return 1
    if public is None:
        print(f"historical reads identical; public sources unreadable ({public_error})")
        return 4
    pub = diff(saved["public"], public)
    if pub:
        print("historical reads identical; public sources changed:\n  " + "\n  ".join(pub))
        return 3
    print("identical")
    return 0


if __name__ == "__main__":
    sys.exit(main())
