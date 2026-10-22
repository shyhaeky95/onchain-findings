#!/usr/bin/env python3
"""Offline reproduction of DefiLlama/dimension-adapters#8812 (Rocket Pool megapool deposits skipped).

Usage:
    python3 reproduce.py                  # verify data, recompute, print one row per claim
    python3 reproduce.py --json           # same, as JSON
    python3 reproduce.py --update-hashes  # refresh manifest hashes after live_verify.py --write

No network, no API key, standard library only.

Each row compares the report's own wording ("stated", fixed in REPORT below, copied from the
issue text) with what the evidence gives ("computed"). Status:
    MATCH     the evidence supports a statement of the report
    ERRATUM   the statement is wrong, and the evidence equals the documented correction
    RECORDED  a value that exists only as a kept result; nothing here re-derives it
    OBSERVED  a fact about the sources after the fix; not a statement of the report
    FAIL      anything else
Exit 0 when no row is FAIL; exit 1 on any FAIL or data-integrity error.
"""
import argparse
import datetime
import hashlib
import json
import sys
from fractions import Fraction
from pathlib import Path

CASE = Path(__file__).resolve().parent
sys.path.insert(0, str(CASE.parent.parent / "lib"))
import evm  # noqa: E402

HEAD, PAY, ACTIVATION, SCAN_FROM = "25772201", "25772216", 24479994, 24416500
WEI = 10 ** 18
ROCKET_STORAGE = "0x1d8f8f00cfa6758d7be78336684788fb0ee0fa46"
CONTRACTS = ("rocketTokenRETH", "rocketMinipoolManager", "rocketDAOProtocolSettingsNetwork",
             "rocketNetworkRevenues", "rocketVault", "rocketRewardsPool")
SATURN_CONTRACTS = ("rocketMegapoolFactory", "rocketMegapoolManager")
CONTROL_INDEXES = (0, 21158, 42316)
T_RETH_DEPOSIT = evm.topic("EtherDeposited(address,uint256,uint256)")
T_REWARDS = evm.topic("RewardsDistributed(uint256,uint256,uint256,uint256,uint256)")
T_VAULT_DEPOSIT = evm.topic("EtherDeposited(string,uint256,uint256)")
T_VAULT_WITHDRAW = evm.topic("EtherWithdrawn(string,uint256,uint256)")
REWARDS_POOL_TOPIC = evm.hex32(evm.keccak256(b"rocketRewardsPool"))
RD = "RewardsDistributed, any emitter"

# Figures exactly as written in the issue text.
REPORT = {
    "emitters": 32,
    "megapools": 31,
    "deposits": 53,
    "skipped_wei": 78234366859157413918,
    "minipool_count": 42317,
    # (index, address as abbreviated in the issue, node deposit balance in ETH, node fee in %)
    "control_minipools": [(0, "0x56b678c9", "3916", 8.0, 14.0), (21158, "0x5077d219", "8697", 8.0, 14.0),
                          (42316, "0x3b7fe930", "22fb", 8.0, 5.0)],
    "control_logs": 11,
    "shares": {"getVoterShare()": 90000000000000000, "getNodeShare()": 50000000000000000,
               "getProtocolDAOShare()": 0, "getCurrentVoterShare()": 90000000000000000},
    "voter_wei": 8187317462004845634,
    "node_wei": 17544251704296097817,
    "pdao_wei": 0,
    "voter_over_reth": "0.104651162791",
    "borrowed_pct": "87.5000",
    "vault_withdrawn_wei": 4437058101524732668,
    "vault_balance_wei": 3750259360480112966,
    "other_logs": 5,
    "other_second_field_eth": "426.928010500",
    "unfiltered_voter_wei": 435115327962004845634,
    "overstatement": "53.1",
    "activation_date": "2026-02-17",
    "pr_2765_merged": "2025-04-03",
    "pr_3519_merged": "2025-06-22",
    "storage": ROCKET_STORAGE,
    "settings_address": "0x67fd03a5095197d1ad1f932bc55e022c420b1153",
    "revenues_address": "0x9d9708da8e0200dd8dd9ad09e0aaf184ad260842",
    "deposit_topic0": "0xef51b4c870b8b0100eae2072e91db01222a303072af3728e58c9d4d2da33127f",
    "measured_day": "2026-08-17",
    "supply_side_text": "Total ETH staking rewards are distributed to rETH stakers and minipool depositors",
    "revenue_text": "Rocket Pool protocol doesn't take any fees or rewards cut.",
    "api_strings": {"UserFees": "Percentage of rewards paid to protocol", "ProtocolRevenue": "Fee from users rewards"},
}

REREAD = "Re-read at block, 2026-10-22 snapshot"
PUBLIC = "Public sources as of 2026-10-22 (snapshot)"
ARITH = "Arithmetic"


class DataError(Exception):
    """Inputs are missing, unlisted, inconsistent, or differ from the manifest."""


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(case):
    return json.loads((case / "MANIFEST.json").read_text(encoding="utf-8"))


def verify_data(case):
    manifest = load_manifest(case)
    listed = {f["path"] for f in manifest["files"]}
    present = {p.relative_to(case).as_posix() for p in (case / "data").rglob("*") if p.is_file()}
    if present - listed:
        raise DataError(f"files not in MANIFEST.json: {sorted(present - listed)}")
    for f in manifest["files"]:
        p = case / f["path"]
        if not p.is_file():
            raise DataError(f"missing: {f['path']}")
        if p.stat().st_size != f["bytes"] or sha256(p) != f["sha256"]:
            raise DataError(f"checksum mismatch: {f['path']}")
    return manifest


def eth(wei):
    sign = "-" if wei < 0 else ""
    wei = abs(wei)
    return f"{sign}{wei // WEI}.{wei % WEI:018d}"


def utc_day(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d")


class Snapshot:
    def __init__(self, data):
        self.data = data
        self.calls, self.logs = {}, {}
        for c in data["calls"]:
            if c["label"] in self.calls:
                raise DataError(f"two calls labelled {c['label']}")
            self.calls[c["label"]] = c
        for q in data["logs"]:
            if q["label"] in self.logs:
                raise DataError(f"two log queries labelled {q['label']}")
            self.logs[q["label"]] = evm.unpack(q)

    def reverted(self, label):
        return self.calls[label].get("reverted") is True

    def word(self, label):
        c = self.calls[label]
        if c.get("reverted"):
            raise DataError(f"{label} reverted")
        return int(c["result"], 16)

    def address(self, label):
        return evm.address_of(self.calls[label]["result"])


def expected_reads(s):
    """Every call and log query the snapshot must hold, rebuilt from the report's blocks and the
    addresses the snapshot itself resolved: {label: (to, calldata, block)}, {label: (address, topics, from, to)}."""
    calls, queries = {}, {}

    def get_address(name, block):
        key = evm.storage_key("contract.address", name.encode())
        calls[f"getAddress(contract.address{name})@{block}"] = (ROCKET_STORAGE, evm.calldata("getAddress(bytes32)", key[2:]), int(block))
    for name in CONTRACTS:
        get_address(name, HEAD)
    for name in ("rocketVault", "rocketRewardsPool"):
        get_address(name, PAY)
    for name in SATURN_CONTRACTS:
        get_address(name, ACTIVATION - 1)
        get_address(name, ACTIVATION)
    addr = {name: s.address(f"getAddress(contract.address{name})@{HEAD}") for name in CONTRACTS}
    head, pay = int(HEAD), int(PAY)
    queries[RD] = (None, [T_REWARDS], SCAN_FROM, pay)
    emitters = sorted({l["address"] for l in s.logs[RD]})
    for e in emitters:
        key = evm.storage_key("megapool.exists", bytes.fromhex(e[2:]))
        calls[f"megapool.exists({e})"] = (ROCKET_STORAGE, evm.calldata("getBool(bytes32)", key[2:]), head)
    for mp in (e for e in emitters if s.word(f"megapool.exists({e})") == 1):
        for fn in ("getNodeDepositBalance()", "getNodeFee()"):
            calls[f"{fn}@{mp}"] = (mp, evm.selector(fn), head)
        calls[f"getMinipoolExists({mp})"] = (addr["rocketMinipoolManager"],
                                             evm.calldata("getMinipoolExists(address)", evm.word_address(mp)[2:]), head)
        queries[f"rETH EtherDeposited from {mp}"] = (addr["rocketTokenRETH"], [T_RETH_DEPOSIT, evm.word_address(mp)], 0, pay)
    calls["getMinipoolCount()"] = (addr["rocketMinipoolManager"], evm.selector("getMinipoolCount()"), head)
    for i in CONTROL_INDEXES:
        calls[f"getMinipoolAt({i})"] = (addr["rocketMinipoolManager"], evm.calldata("getMinipoolAt(uint256)", evm.enc_uint(i)), head)
        mp = s.address(f"getMinipoolAt({i})")
        for fn in ("getNodeDepositBalance()", "getNodeFee()"):
            calls[f"{fn}@minipool {i}"] = (mp, evm.selector(fn), head)
        if i == CONTROL_INDEXES[-1]:
            queries[f"rETH EtherDeposited from minipool {i}"] = (addr["rocketTokenRETH"], [T_RETH_DEPOSIT, evm.word_address(mp)], 0, pay)
    for fn in ("getVoterShare()", "getNodeShare()", "getProtocolDAOShare()"):
        calls[fn] = (addr["rocketDAOProtocolSettingsNetwork"], evm.selector(fn), head)
    calls["getCurrentVoterShare()"] = (addr["rocketNetworkRevenues"], evm.selector("getCurrentVoterShare()"), head)
    calls["rocketVault.balanceOf(rocketRewardsPool)"] = (addr["rocketVault"], evm.calldata("balanceOf(string)", evm.enc_string("rocketRewardsPool")), pay)
    calls["getPendingVoterShare()"] = (addr["rocketRewardsPool"], evm.selector("getPendingVoterShare()"), pay)
    queries["rocketVault EtherDeposited by rocketRewardsPool"] = (addr["rocketVault"], [T_VAULT_DEPOSIT, REWARDS_POOL_TOPIC], 0, pay)
    queries["rocketVault EtherWithdrawn by rocketRewardsPool"] = (addr["rocketVault"], [T_VAULT_WITHDRAW, REWARDS_POOL_TOPIC], 0, pay)
    return calls, queries


def check_structure(s):
    """Each call is the call its label names (target, calldata, block), and each log query has the filter and
    block range its label names, so a row cannot rest on a read of something else."""
    for label in ("getAddress(contract.addressrocketVault)", "getAddress(contract.addressrocketRewardsPool)"):
        if s.address(f"{label}@{HEAD}") != s.address(f"{label}@{PAY}"):
            raise DataError(f"{label} differs between {HEAD} and {PAY}")
    calls, queries = expected_reads(s)
    if set(calls) != set(s.calls) or set(queries) != set(s.logs):
        raise DataError(f"reads differ from the expected set: {sorted(set(calls) ^ set(s.calls) | set(queries) ^ set(s.logs))[:5]}")
    for label, (to, data, block) in calls.items():
        c = s.calls[label]
        if (c["to"], c["data"], c["block"]) != (to, data, block):
            raise DataError(f"{label} is not the call its label names")
        if str(block) not in s.data["blocks"]:
            raise DataError(f"no header for block {block}")
    for q in s.data["logs"]:
        if (q["address"], q["topics"], q["from"], q["to"]) != queries[q["label"]]:
            raise DataError(f"{q['label']} is not the query its label names")
        for l in evm.unpack(q):
            if not evm.matches_filter(l, q["address"], q["topics"]) or not q["from"] <= l["blockNumber"] <= q["to"]:
                raise DataError(f"{q['label']}: a log outside its filter")


class Rows:
    def __init__(self):
        self.rows = []

    def _add(self, cid, claim, stated, computed, level, status, note):
        self.rows.append({"id": cid, "claim": claim, "stated": stated, "computed": computed,
                          "level": level, "status": status, "note": note})

    def check(self, cid, claim, stated, computed, level, conditions, note=""):
        """conditions: named checks, all of which the statement relies on."""
        broken = [name for name, ok in conditions.items() if not ok]
        if broken:
            note = f"does not hold: {', '.join(broken)}. " + note
        self._add(cid, claim, stated, computed, level, "FAIL" if broken else "MATCH", note)

    def record(self, cid, claim, stated, computed, level, note):
        """A statement nothing here re-derives: kept only as the report wrote it."""
        self._add(cid, claim, stated, computed, level, "RECORDED", note)

    def observe(self, cid, claim, stated, computed, level, conditions, note=""):
        broken = [name for name, ok in conditions.items() if not ok]
        if broken:
            note = f"does not hold: {', '.join(broken)}. " + note
        self._add(cid, claim, stated, computed, level, "FAIL" if broken else "OBSERVED", note)


def results(case=CASE):
    verify_data(case)
    s = Snapshot(json.loads((case / "data" / "live_snapshot.json").read_text(encoding="utf-8")))
    check_structure(s)
    blocks = s.data["blocks"]
    rewards = s.logs["RewardsDistributed, any emitter"]
    emitters = sorted({l["address"] for l in rewards})
    flagged = [e for e in emitters if s.word(f"megapool.exists({e})") == 1]
    mega = [l for l in rewards if l["address"] in flagged]
    other = [l for l in rewards if l["address"] not in flagged]
    # RewardsDistributed(nodeAmount, voterAmount, rethAmount, protocolDaoAmount, time)
    node, voter, reth, pdao = (sum(evm.words(l["data"])[i] for l in mega) for i in range(4))
    deposits = [l for m in flagged for l in s.logs[f"rETH EtherDeposited from {m}"]]
    big = sorted(a for a in (evm.words(l["data"])[0] for l in deposits) if a >= 16 * WEI)
    skipped = sum(evm.words(l["data"])[0] for l in deposits)
    per_address = max((len(s.logs[f"rETH EtherDeposited from {m}"]) for m in flagged), default=0)
    r = Rows()

    # L: on-chain facts, re-read at the blocks the report names.
    r.check("L1", "megapools among the RewardsDistributed emitters (protocol flag megapool.exists)",
            f"{REPORT['megapools']} of {REPORT['emitters']}", f"{len(flagged)} of {len(emitters)}", REREAD,
            {"31 flagged": len(flagged) == REPORT["megapools"], "32 emitters": len(emitters) == REPORT["emitters"]},
            "The flag is rocketStorage.getBool(keccak256('megapool.exists' + address)) at block 25,772,201.")
    both = [m for m in flagged if s.reverted(f"getNodeDepositBalance()@{m}") and s.reverted(f"getNodeFee()@{m}")]
    r.check("L2", "getNodeDepositBalance() and getNodeFee() revert on every megapool, at block 25,772,201",
            "31 of 31 revert on both", f"{len(both)} of {len(flagged)} revert on both", REREAD,
            {"every megapool reverts on both": len(both) == len(flagged), "31 megapools": len(flagged) == REPORT["megapools"]},
            "In the adapter these calls use permitFailure: true, so a revert becomes null and the deposit is skipped.")
    not_minipool = [m for m in flagged if s.word(f"getMinipoolExists({m})") == 0]
    r.check("L3", "getMinipoolExists() for the megapools", "0 for all 31", f"0 for {len(not_minipool)} of {len(flagged)}",
            REREAD, {"0 for every megapool": len(not_minipool) == len(flagged) == REPORT["megapools"]})
    count = s.word("getMinipoolCount()")
    r.check("L4", "positive control: rocketMinipoolManager.getMinipoolCount()", REPORT["minipool_count"], count, REREAD,
            {"count": count == REPORT["minipool_count"]})
    got, conds = [], {}
    for i, head, tail, bal, fee in REPORT["control_minipools"]:
        a = s.address(f"getMinipoolAt({i})")
        b = s.word(f"getNodeDepositBalance()@minipool {i}") / WEI
        f = s.word(f"getNodeFee()@minipool {i}") / 1e16
        got.append(f"{a[:10]}…{a[-4:]} {b:.1f} ETH {f:.1f}%")
        conds[f"index {i} address"] = a.startswith(head) and a.endswith(tail)
        conds[f"index {i} values"] = (round(b, 1), round(f, 1)) == (bal, fee)
    r.check("L5", "positive control: minipools at index 0, 21,158 and 42,316 answer both calls",
            "0x56b678c9…3916 8.0/14.0% · 0x5077d219…8697 8.0/14.0% · 0x3b7fe930…22fb 8.0/5.0%", " · ".join(got), REREAD, conds)
    r.check("L6", "rETH EtherDeposited from the 31 megapools, block 0 to 25,772,216",
            f"{REPORT['deposits']} logs, {eth(REPORT['skipped_wei'])} ETH", f"{len(deposits)} logs, {eth(skipped)} ETH", REREAD,
            {"53 logs": len(deposits) == REPORT["deposits"], "total to the wei": skipped == REPORT["skipped_wei"]},
            f"Largest per-address count: {per_address} (the report: no address reached a 1,000-log page; implied by the 53-log total). "
            "A lower bound: only megapools that emitted RewardsDistributed are in the seed set.")
    control = s.logs["rETH EtherDeposited from minipool 42316"]
    r.check("L7", "positive control: the same query for minipool 0x3b7fe930…22fb", REPORT["control_logs"], len(control),
            REREAD, {"11 logs": len(control) == REPORT["control_logs"]})
    r.check("L8", "Σ rethAmount over the megapools' RewardsDistributed logs",
            f"{REPORT['deposits']} logs, {eth(REPORT['skipped_wei'])} ETH, residual 0 wei",
            f"{len(mega)} logs, {eth(reth)} ETH, residual {reth - skipped} wei", REREAD,
            {"53 logs": len(mega) == REPORT["deposits"], "equals the report's total": reth == REPORT["skipped_wei"],
             "residual 0 against L6": reth == skipped})
    pairs_dep = sorted((evm.address_of(l["topics"][1]), l["transactionHash"], evm.words(l["data"])[0]) for l in deposits)
    pairs_rd = sorted((l["address"], l["transactionHash"], evm.words(l["data"])[2]) for l in mega)
    matched = len(set(pairs_dep) & set(pairs_rd))
    dep_time = {(evm.address_of(l["topics"][1]), l["transactionHash"], evm.words(l["data"])[0]): evm.words(l["data"])[1] for l in deposits}
    rd_time = {(l["address"], l["transactionHash"], evm.words(l["data"])[2]): evm.words(l["data"])[4] for l in mega}
    same_time = sum(dep_time[k] == rd_time[k] for k in set(dep_time) & set(rd_time))
    r.check("L9", "deposits pair one-to-one with RewardsDistributed on (address, transactionHash, amount)",
            "53 of 53, none unmatched on either side",
            f"{matched} matched; {len(pairs_dep) - matched} deposits and {len(pairs_rd) - matched} distributions unmatched",
            REREAD, {"53 matched": matched == REPORT["deposits"], "no unmatched deposit": len(pairs_dep) == matched,
                     "no unmatched distribution": len(pairs_rd) == matched,
                     "the time fields agree in every matched pair": same_time == matched},
            f"The time field (RewardsDistributed's fifth word, EtherDeposited's second) agrees in {same_time} pairs, which "
            "also confirms the field order taken from RocketMegapoolDelegate.sol. "
            f"{len(big)} of the deposits are 16 ETH or more ({', '.join(f'{a / WEI:.2f}' for a in big)} ETH, "
            f"{float(Fraction(sum(big), skipped or 1)) * 100:.0f}% of the total): through the minipool branch they would "
            "have lost 16 or 24 ETH to the capital-repayment subtraction.")
    shares = {k: s.word(k) for k in REPORT["shares"]}
    settings = s.address(f"getAddress(contract.addressrocketDAOProtocolSettingsNetwork)@{HEAD}")
    revenues = s.address(f"getAddress(contract.addressrocketNetworkRevenues)@{HEAD}")
    head_day = utc_day(blocks[HEAD]["timestamp"])
    r.check("L10", "voter / node / protocol DAO shares and the current voter share, read via RocketStorage at head 25,772,201 on 2026-08-17",
            f"9% / 5% / 0 · 9%; settings {REPORT['settings_address'][:10]}…, revenues {REPORT['revenues_address'][:10]}…",
            f"{shares['getVoterShare()'] / 1e16:g}% / {shares['getNodeShare()'] / 1e16:g}% / "
            f"{shares['getProtocolDAOShare()']} · {shares['getCurrentVoterShare()'] / 1e16:g}%; settings {settings[:10]}…, "
            f"revenues {revenues[:10]}…; block date {head_day}", REREAD,
            {**{k: shares[k] == v for k, v in REPORT["shares"].items()},
             "settings contract": settings == REPORT["settings_address"], "revenues contract": revenues == REPORT["revenues_address"],
             "measured on 2026-08-17": head_day == REPORT["measured_day"]})
    r.check("L11", "Σ voterAmount / nodeAmount / protocolDaoAmount over the same 53 distributions",
            f"{eth(REPORT['voter_wei'])} / {eth(REPORT['node_wei'])} / {REPORT['pdao_wei']}",
            f"{eth(voter)} / {eth(node)} / {pdao}", REREAD,
            {"voter": voter == REPORT["voter_wei"], "node": node == REPORT["node_wei"], "protocol DAO": pdao == REPORT["pdao_wei"]})
    vd = sum(evm.words(l["data"])[0] for l in s.logs["rocketVault EtherDeposited by rocketRewardsPool"])
    vw = sum(evm.words(l["data"])[0] for l in s.logs["rocketVault EtherWithdrawn by rocketRewardsPool"])
    bal = s.word("rocketVault.balanceOf(rocketRewardsPool)")
    pending = s.word("getPendingVoterShare()")
    r.check("L12", "rocketVault, name rocketRewardsPool, at block 25,772,216: Σ deposited − Σ withdrawn = balance",
            f"{eth(REPORT['voter_wei'])} − {eth(REPORT['vault_withdrawn_wei'])} = {eth(REPORT['vault_balance_wei'])}; "
            "getPendingVoterShare() the same",
            f"{eth(vd)} − {eth(vw)} = {eth(vd - vw)}; balanceOf {eth(bal)}; getPendingVoterShare() {eth(pending)}", REREAD,
            {"deposits equal Σ voterAmount": vd == voter, "withdrawals": vw == REPORT["vault_withdrawn_wei"],
             "residual 0 wei": vd - vw == bal, "pending voter share equals balance": pending == bal},
            "With L11's Σ voterAmount, these fix the balance at the stated figure.")
    other_second = sum(evm.words(l["data"])[1] for l in other)
    unfiltered = sum(evm.words(l["data"])[1] for l in rewards)
    over = Fraction(unfiltered, voter) if voter else Fraction(0)
    r.check("L13", "RewardsDistributed topic0 without filtering the emitter",
            f"another contract, {REPORT['other_logs']} logs, second field {REPORT['other_second_field_eth']} ETH; "
            f"{eth(REPORT['unfiltered_voter_wei'])} vs {eth(REPORT['voter_wei'])}, {REPORT['overstatement']}x",
            f"{len({l['address'] for l in other})} other contract, {len(other)} logs, second field {other_second / WEI:.9f} ETH; "
            f"{eth(unfiltered)} vs {eth(voter)}, {float(over):.1f}x", REREAD,
            {"one other emitter": len({l["address"] for l in other}) == 1, "5 logs": len(other) == REPORT["other_logs"],
             "second field": f"{other_second / WEI:.9f}" == REPORT["other_second_field_eth"],
             "unfiltered total": unfiltered == REPORT["unfiltered_voter_wei"]})
    act = {n: (s.address(f"getAddress(contract.address{n})@{ACTIVATION - 1}"), s.address(f"getAddress(contract.address{n})@{ACTIVATION}"))
           for n in ("rocketMegapoolFactory", "rocketMegapoolManager")}
    zero = "0x" + "0" * 40
    day = utc_day(blocks[str(ACTIVATION)]["timestamp"])
    r.check("L14", "RocketStorage began serving the Saturn 1 contracts at block 24,479,994 (2026-02-17)",
            "unset at 24,479,993, set at 24,479,994; 2026-02-17",
            "; ".join(f"{n}: {a[:10]}… → {b[:10]}…" for n, (a, b) in act.items()) + f"; block date {day}", REREAD,
            {**{f"{n} unset before": a == zero for n, (a, b) in act.items()},
             **{f"{n} set at the block": b != zero for n, (a, b) in act.items()},
             "date": day == REPORT["activation_date"]},
            f"Checked for rocketMegapoolFactory and rocketMegapoolManager. The block's time is "
            f"{datetime.datetime.fromtimestamp(blocks[str(ACTIVATION)]['timestamp'], datetime.timezone.utc):%H:%M:%S} UTC.")

    # A: arithmetic on the rows above.
    ratio = Fraction(voter, reth) if reth else Fraction(0)
    r.check("A1", "Σ voterAmount / Σ rethAmount equals 9/86 to 12 digits", f"{REPORT['voter_over_reth']} = 9/86",
            f"{float(ratio):.12f}", f"{ARITH} on L8 and L11",
            {"12 digits": f"{float(ratio):.12f}" == REPORT["voter_over_reth"]},
            f"9/86 = {float(Fraction(9, 86)):.12f}: the 9% voter share over the 86% rETH share.")
    total = node + voter + reth + pdao
    borrowed = Fraction(voter, 1) / Fraction(shares["getVoterShare()"], WEI) if shares["getVoterShare()"] else Fraction(0)
    share = borrowed / total * 100 if total else Fraction(0)
    r.check("A2", "the borrowed portion of the distributed total (Σ voterAmount / voter share)",
            f"{REPORT['borrowed_pct']}%, consistent with a 4/32 bond", f"{float(share):.4f}%",
            f"{ARITH} on L10 and L11", {"87.5000%": f"{float(share):.4f}" == REPORT["borrowed_pct"]},
            "A 4 ETH bond in a 32 ETH validator borrows 28/32 = 87.5%.")
    r.check("A3", "the unfiltered sum overstates the voter share", f"{REPORT['overstatement']}x", f"{float(over):.1f}x",
            f"{ARITH} on L11 and L13", {"53.1x": f"{float(over):.1f}" == REPORT["overstatement"]})
    r.check("A4", "the EtherDeposited topic0 the report prints is keccak256 of the rETH event signature",
            REPORT["deposit_topic0"][:18] + "…", T_RETH_DEPOSIT[:18] + "…", f"{ARITH} (Keccak-256 of EtherDeposited(address,uint256,uint256))",
            {"topic0": T_RETH_DEPOSIT == REPORT["deposit_topic0"]},
            "The L6 queries use this topic (checked when the snapshot is loaded).")

    # P: the adapter and its history, as published.
    pub = s.data["public"]
    pre = pub["adapter_before_fix"]
    r.check("P1", "fees/rocketpool.ts before the fix: both calls with permitFailure: true, skip when either is null",
            "yes", f"permitFailure {pre['permit_failure']}, skip branch {pre['skips_when_either_is_null']}, "
            f"megapool path {pre['reads_megapool_exists_flag']}", PUBLIC,
            {"calls getNodeDepositBalance": pre["calls_getNodeDepositBalance"], "calls getNodeFee": pre["calls_getNodeFee"],
             "permitFailure: true": pre["permit_failure"], "skip branch": pre["skips_when_either_is_null"],
             "no megapool path": not pre["reads_megapool_exists_flag"]},
            f"File at {pub['pr_8833']['parent_sha'][:10]}, the parent of the fix's merge commit.")
    r.check("P2", "the >= 24e18 / >= 16e18 subtraction (a minipool repaying borrowed capital)", "in the adapter",
            pre["subtracts_24_and_16_eth"], PUBLIC, {"present before the fix": pre["subtracts_24_and_16_eth"]})
    p1, p2 = pub["pr_2765"], pub["pr_3519"]
    r.check("P3", "#2765 introduced the minipool-based adapter (merged 2025-04-03); #3519 added revenue = 0 (2025-06-22)",
            "2025-04-03 · 2025-06-22", f"{(p1['merged_at'] or '')[:10]} ({p1['adapter_status']}) · "
            f"{(p2['merged_at'] or '')[:10]} (adds dailyRevenue: 0 {p2['adds_zero_revenue']})", PUBLIC,
            {"#2765 merged 2025-04-03": (p1["merged_at"] or "").startswith(REPORT["pr_2765_merged"]),
             "#2765 adds the file": p1["adapter_status"] == "added", "#2765 adds getNodeFee": p1["adds_getNodeFee"],
             "#3519 merged 2025-06-22": (p2["merged_at"] or "").startswith(REPORT["pr_3519_merged"]),
             "#3519 adds dailyRevenue: 0": p2["adds_zero_revenue"]},
            "Both predate the Saturn 1 activation (L14).")

    r.check("P4", "the methodology strings in fees/rocketpool.ts: SupplySideRevenue still describes the pre-Saturn split; "
            "only Fees, Revenue and SupplySideRevenue appear",
            f"'{REPORT['supply_side_text']}'; labels Fees, Revenue, SupplySideRevenue; no UserFees, no ProtocolRevenue",
            f"SupplySideRevenue text {pre['supply_side_text_pre_saturn']}, Revenue text {pre['revenue_text_no_cut']}, "
            f"methodology labels {pre['methodology_labels']}, UserFees anywhere {pre['has_user_fees_label']}, "
            f"ProtocolRevenue anywhere {pre['has_protocol_revenue_label']}", PUBLIC,
            {"SupplySideRevenue text": pre["supply_side_text_pre_saturn"], "Revenue text": pre["revenue_text_no_cut"],
             "exactly three methodology labels": pre["methodology_labels"] == ["Fees", "Revenue", "SupplySideRevenue"],
             "no UserFees": not pre["has_user_fees_label"], "no ProtocolRevenue": not pre["has_protocol_revenue_label"]},
            "Same file as P1.")
    r.record("R1", "two further methodology strings in DefiLlama's API response of 2026-08-17",
             "; ".join(f"{k}: '{v}'" for k, v in REPORT["api_strings"].items()), "not re-read", "Recorded (report text)",
             "They come from api.llama.fi, not from the adapter, and the report says it does not rely on them. This case "
             "keeps no DefiLlama data, so they stand only as the report quoted them.")

    # O: what happened after the report. Observations, not statements of the report.
    pr, post, issue = pub["pr_8833"], pub["adapter_after_fix"], pub["issue_8812"]
    r.observe("O1", "PR #8833 merged, closing #8812, and its description uses the report's figure",
              "merged 2026-08-18", f"merged {pr['merged_at']}; closes #8812: {pr['body_closes_8812']}; "
              f"cites 78.23 ETH: {pr['body_cites_78_23_eth']}", PUBLIC,
              {"merged 2026-08-18": (pr["merged_at"] or "").startswith("2026-08-18"), "closes #8812": pr["body_closes_8812"],
               "cites 78.23": pr["body_cites_78_23_eth"]})
    r.observe("O2", "the adapter after the fix reads RewardsDistributed from emitters flagged by megapool.exists",
              "megapool path added", f"megapool.exists {post['reads_megapool_exists_flag']}, scoped RewardsDistributed "
              f"{post['reads_rewards_distributed_of_megapools']}, minipool skip branch kept {post['skips_when_either_is_null']}",
              PUBLIC, {"flag": post["reads_megapool_exists_flag"], "scoped to megapools": post["reads_rewards_distributed_of_megapools"]},
              f"File at merge commit {pr['merge_commit_sha'][:10]}. It counts all four legs as fees and leaves the voter-share "
              "classification to a follow-up.")
    r.observe("O3", "issue #8812 closed as completed", "closed 2026-08-18",
              f"{issue['state']} / {issue['state_reason']} / {issue['closed_at']}", PUBLIC,
              {"closed": issue["state"] == "closed", "completed": issue["state_reason"] == "completed",
               "2026-08-18": (issue["closed_at"] or "").startswith("2026-08-18")})
    return r.rows


def summary(rows):
    counts = {k: sum(x["status"] == k for x in rows) for k in ("MATCH", "ERRATUM", "RECORDED", "OBSERVED", "FAIL")}
    by_level = {}
    for x in rows:
        if x["status"] == "MATCH":
            kind = x["level"].split(" on ")[0].split(" (")[0]
            by_level[kind] = by_level.get(kind, 0) + 1
    return counts, by_level


def update_hashes(case=CASE):
    manifest = load_manifest(case)
    for f in manifest["files"]:
        p = case / f["path"]
        f["sha256"], f["bytes"] = sha256(p), p.stat().st_size
    (case / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    verify_data(case)


def main(argv=None, case=CASE):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--update-hashes", action="store_true")
    args = ap.parse_args(argv)
    try:
        if args.update_hashes:
            update_hashes(case)
            print("manifest hashes updated")
            return 0
        rows = results(case)
    except (DataError, KeyError, ValueError) as e:
        print(f"DATA ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for x in rows:
            print(f"{x['status']:<8} {x['id']:<3} {x['claim']}")
            print(f"         stated: {x['stated']}  computed: {x['computed']}  [{x['level']}]")
            if x["note"]:
                print(f"         {x['note']}")
        counts, by_level = summary(rows)
        print(f"\nreport statements: {counts['MATCH']} match, {counts['ERRATUM']} errata, "
              f"{counts['RECORDED']} recorded only; post-fix observations: {counts['OBSERVED']}; fail: {counts['FAIL']}")
        print("matches by evidence: " + ", ".join(f"{k}: {v}" for k, v in by_level.items()))
    return 1 if any(x["status"] == "FAIL" for x in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
