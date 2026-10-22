#!/usr/bin/env python3
"""Re-read the on-chain facts behind DefiLlama/DefiLlama-Adapters#20659 at their historical
blocks, and record what the public sources say at the time of the run.

Usage:
    BSC_ARCHIVE_RPC=<archive endpoint> python3 live_verify.py           # print only
    BSC_ARCHIVE_RPC=<archive endpoint> python3 live_verify.py --check   # compare with data/live_snapshot.json
    BSC_ARCHIVE_RPC=<archive endpoint> python3 live_verify.py --write   # also refresh data/live_snapshot.json

--check exit codes:
    0  everything matches the committed snapshot
    1  a historical on-chain read differs (that must never happen)
    3  history matches; only the public sources have changed since the snapshot
    4  history matches; a public source could not be read
The on-chain reads come first, so a failing public source never hides the historical check.

The on-chain part needs an endpoint that serves historical state at these blocks; public
BSC endpoints return "missing trie node" or require a key. The endpoint URL is never
written to disk. After --write, run `python3 reproduce.py --update-hashes`.
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
SNAPSHOT = CASE / "data" / "live_snapshot.json"

TOKEN = "0xac531eb26ca1d21b85126de8fb87e80e09002dcf"
PEGGED_SAND = "0x67b725d7e342d7b611fa85e859df9697d9378b2e"
POOL_MANAGER = "0x28e2ea090877bf75740558f6bfb36a5ffee9e9df"
MAJOR = {
    "USDT": "0x55d398326f99059fF775485246999027B3197955",
    "USDC": "0x8AC76a51cc950d9822d68b83fE1Ad97B32Cd580d",
    "WBNB": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
    "BTCB": "0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c",
}
# Tokens the report cites as already blacklisted for the same reason (Ethereum list).
BLOTIX = "0xbdb37597c7e84d898a5536fbb6e4a5c2bcac640b"
SAFEMONEY = "0xc4abeddacfa65e25d6c942861fb3bdc9c00eeb13"
# 117,739,591: block the report names. 117,733,791: head recorded by the original run.
BLOCKS = [117739591, 117733791]
ADAPTER_PATH = "projects/uniswap-v4/index.js"
ADAPTER_COMMITS = {
    "before_fix": "a4b0919f20e270b0d6fdb142363296e3465c7c3b",
    "fix_20690": "5a90cdeaf0362862cadafcf6b38241a024f915be",
}
TVL_DAYS = ["2026-08-22", "2026-08-23", "2026-08-24"]
GITHUB_API = "https://api.github.com/repos/DefiLlama/DefiLlama-Adapters"

SEL = {"name": "0x06fdde03", "symbol": "0x95d89b41", "decimals": "0x313ce567",
       "totalSupply": "0x18160ddd", "balanceOf": "0x70a08231"}


def http(url, payload=None, timeout=60):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"user-agent": "onchain-findings", "content-type": "application/json"}
    with urllib.request.urlopen(urllib.request.Request(url, data, headers), timeout=timeout) as r:
        return r.read()


class Rpc:
    def __init__(self, url):
        self.url = url

    def raw(self, method, params):
        return json.loads(http(self.url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))

    def result(self, method, params):
        reply = self.raw(method, params)
        if "error" in reply:
            raise RuntimeError(f"{method} failed: {reply['error']}")
        return reply["result"]

    def call(self, to, data, block):
        return self.result("eth_call", [{"to": to, "data": data}, hex(block)])


def decode_string(hexdata):
    b = bytes.fromhex(hexdata[2:])
    if len(b) >= 64 and int.from_bytes(b[:32], "big") == 32:
        n = int.from_bytes(b[32:64], "big")
        return b[64:64 + n].decode("utf-8", "replace")
    return b.rstrip(b"\0").decode("utf-8", "replace")


def units(hexdata, decimals=18):
    return int(hexdata, 16) / 10 ** decimals


def balance_of(rpc, token, holder, block, decimals=18):
    return units(rpc.call(token, SEL["balanceOf"] + holder[2:].lower().rjust(64, "0"), block), decimals)


def archive_checks(rpc, block):
    """The endpoint must serve history: no code before deployment, an error for a future block."""
    code_then = rpc.result("eth_getCode", [POOL_MANAGER, hex(block)])
    code_genesis = rpc.result("eth_getCode", [POOL_MANAGER, hex(1)])
    future = rpc.raw("eth_call", [{"to": TOKEN, "data": SEL["totalSupply"]}, hex(10 ** 10)])
    checks = {
        "pool_manager_code_bytes_at_block": (len(code_then) - 2) // 2,
        "pool_manager_code_at_block_1_empty": code_genesis in ("0x", "0x0"),
        "future_block_returns_error": "error" in future,
    }
    if not (checks["pool_manager_code_bytes_at_block"] > 0 and checks["pool_manager_code_at_block_1_empty"]
            and checks["future_block_returns_error"]):
        raise RuntimeError(f"endpoint does not look like an archive node: {checks}")
    return checks


def read_block(rpc, block):
    header = rpc.result("eth_getBlockByNumber", [hex(block), False])
    return {
        "hash": header["hash"],
        "timestamp_utc": datetime.datetime.fromtimestamp(int(header["timestamp"], 16), datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "token": {
            "name": decode_string(rpc.call(TOKEN, SEL["name"], block)),
            "symbol": decode_string(rpc.call(TOKEN, SEL["symbol"], block)),
            "decimals": int(rpc.call(TOKEN, SEL["decimals"], block), 16),
            "total_supply": units(rpc.call(TOKEN, SEL["totalSupply"], block)),
            "pool_manager_balance": balance_of(rpc, TOKEN, POOL_MANAGER, block),
        },
        "pegged_sand": {
            "name": decode_string(rpc.call(PEGGED_SAND, SEL["name"], block)),
            "total_supply": units(rpc.call(PEGGED_SAND, SEL["totalSupply"], block)),
        },
        "pool_manager_major_balances": {k: balance_of(rpc, a, POOL_MANAGER, block) for k, a in MAJOR.items()},
    }


def blacklist(source, chain):
    entry = re.search(r"\n\s*" + chain + r":\s*\{([^{}]*)\}", source)  # chain entries are flat objects
    if not entry or "blacklistedTokens" not in entry.group(1):
        return []
    return [a.lower() for a in re.findall(r"0x[0-9a-fA-F]{40}", entry.group(1).split("blacklistedTokens", 1)[1])]


def public_state():
    prices = {}
    for label, addr in (("token", TOKEN), ("pegged_sand", PEGGED_SAND)):
        prices[label] = json.loads(http(f"https://coins.llama.fi/prices/current/bsc:{addr}"))
    protocol = json.loads(http("https://api.llama.fi/protocol/uniswap-v4", timeout=120))
    series = {
        datetime.datetime.fromtimestamp(p["date"], datetime.timezone.utc).strftime("%Y-%m-%d"): p["totalLiquidityUSD"]
        for p in protocol["chainTvls"]["Binance"]["tvl"]
    }
    adapters = {}
    for label, sha in ADAPTER_COMMITS.items():
        src = http(f"https://raw.githubusercontent.com/DefiLlama/DefiLlama-Adapters/{sha}/{ADAPTER_PATH}")
        text = src.decode("utf-8")
        bsc, eth = blacklist(text, "bsc"), blacklist(text, "ethereum")
        adapters[label] = {
            "commit": sha,
            "sha256": hashlib.sha256(src).hexdigest(),
            "bsc_blacklist_entries": len(bsc),
            "token_blacklisted": TOKEN in bsc,
            "ethereum_blacklists_blotix_and_safemoney": BLOTIX in eth and SAFEMONEY in eth,
        }
    issue = json.loads(http(f"{GITHUB_API}/issues/20659"))
    pr = json.loads(http(f"{GITHUB_API}/pulls/20690"))
    return {
        "coins_llama_current": prices,
        "defillama_bsc_tvl_now": {d: series.get(d) for d in TVL_DAYS},
        "adapter": adapters,
        "issue_20659": {"state": issue["state"], "state_reason": issue.get("state_reason"),
                        "closed_at": issue.get("closed_at")},
        "pr_20690": {"merged_at": pr.get("merged_at"), "merge_commit_sha": pr.get("merge_commit_sha")},
    }


def compare(old, new, path=""):
    """List the leaf paths where two snapshots differ."""
    if isinstance(old, dict) and isinstance(new, dict):
        out = []
        for k in sorted(set(old) | set(new)):
            out += compare(old.get(k), new.get(k), f"{path}/{k}")
        return out
    return [] if old == new else [f"{path}: {old!r} -> {new!r}"]


def collect(rpc):
    """On-chain reads first; a public-source failure is recorded, not raised."""
    snap = {
        "checked_at_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rpc": "archive endpoint supplied via BSC_ARCHIVE_RPC (URL not recorded)",
        "archive_checks": archive_checks(rpc, BLOCKS[0]),
        "blocks": {str(b): read_block(rpc, b) for b in BLOCKS},
    }
    try:
        snap["public"] = public_state()
    except Exception as e:  # network, rate limit, changed response shape
        snap["public_error"] = f"{type(e).__name__}: {e}"
    return snap


def check(snap, old):
    history = compare({k: old.get(k) for k in ("archive_checks", "blocks")},
                      {k: snap[k] for k in ("archive_checks", "blocks")})
    for line in history:
        print(f"HISTORICAL READ DIFFERS {line}")
    if history:
        return 1
    print(f"historical reads match the snapshot of {old['checked_at_utc']}")
    if "public_error" in snap:
        print(f"public sources not checked: {snap['public_error']}")
        return 4
    public = compare(old.get("public"), snap["public"], "/public")
    for line in public:
        print(f"public source changed  {line}")
    return 3 if public else 0


def main(argv=None, rpc=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--write", action="store_true", help="refresh data/live_snapshot.json")
    ap.add_argument("--check", action="store_true", help="compare with the committed snapshot")
    args = ap.parse_args(argv)

    if rpc is None:
        url = os.environ.get("BSC_ARCHIVE_RPC")
        if not url:
            print("BSC_ARCHIVE_RPC is not set; an archive endpoint is required for the on-chain reads.",
                  file=sys.stderr)
            return 2
        rpc = Rpc(url)
    snap = collect(rpc)
    if args.check:
        return check(snap, json.loads(SNAPSHOT.read_text(encoding="utf-8")))
    print(json.dumps(snap, indent=2))
    if args.write:
        if "public_error" in snap:
            print("not written: a public source failed", file=sys.stderr)
            return 4
        SNAPSHOT.write_text(json.dumps(snap, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {SNAPSHOT.relative_to(CASE)}; now run: python3 reproduce.py --update-hashes", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
