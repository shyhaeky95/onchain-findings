#!/usr/bin/env python3
"""Offline reproduction of sky-ecosystem/next-gen-atlas#306 (SKY bought back, totalSupply unchanged).

Usage:
    python3 reproduce.py                  # verify data, recompute, print one row per claim
    python3 reproduce.py --json           # same, as JSON
    python3 reproduce.py --update-hashes  # refresh manifest hashes after live_verify.py --write

No network, no API key, standard library only.

Each row compares the report's own wording ("stated", fixed in REPORT below, copied from the
issue text) with what the evidence gives ("computed"). Status:
    MATCH     the evidence supports a statement of the report
    ERRATUM   the statement is wrong, and the evidence equals the documented correction,
              including every fact the correction relies on
    RECORDED  a statement that exists here only as the report wrote it; nothing re-derives it
    OBSERVED  a fact about the sources after the report; not a statement of the report
    FAIL      anything else, including an erratum whose correction no longer holds
Exit 0 when no row is FAIL; exit 1 on any FAIL or data-integrity error.
"""
import argparse
import bisect
import datetime
import hashlib
import json
import sys
from fractions import Fraction
from pathlib import Path

CASE = Path(__file__).resolve().parent
sys.path.insert(0, str(CASE.parent.parent / "lib"))
import evm  # noqa: E402

B_START, CAST, A_END, SCALE_END = 22950000, 25775271, 25780667, 25578353
WEI = 10 ** 18
CHAINLOG = "0xda0ab1e0017debcd72be8599041a2aa3ba7e740f"
NAMES = ("SKY", "USDS", "MCD_FLAP", "MCD_PAUSE_PROXY", "MCD_SPLIT")
T_TRANSFER = evm.topic("Transfer(address,address,uint256)")
ZERO_WORD = "0x" + "0" * 64
# Dates of the two post-report burns as this case reports them (O3, O4); observations, not report figures.
BURN_DATES = ("2026-09-13", "2026-10-12")
HOLDERS = ("0x0000000000000000000000000000000000000000", "0x000000000000000000000000000000000000dead",
           "0x0000000000000000000000000000000000000001")

# Figures exactly as written in the issue text.
REPORT = {
    "cast_utc": "2026-08-17T14:02:23Z",
    "a_end_utc": "2026-08-18T08:05:47Z",
    "a_days": "0.75",
    "a_kicks": 16,
    "a_usds": 52800,
    "a_sky": "976,150.62",
    "a_burns": 0,
    "supply_a": 23462665147365966560979671451,
    "b_start_day": "2025-07-19",
    "b_end_day": "2026-08-18",
    "b_days": 395,
    "b_burns": 3,
    "b_burned_wei": 1002,
    "b_mints": 0,
    "supply_b_start_tail": "672453",
    "supply_b_end_tail": "671451",
    "b_burn_day": "2026-01-16",
    "b_burn_minutes": 18,
    "b_burn_amounts": [1, 1, 1000],
    "scale_kicks": 8656,
    "scale_sky": "868,204,202.24",
    "split_burn": "0.55",
    "falsifier_sky": "177,481.93",
    "atlas_absent": ["non-issuance", "not issued", "no explicit burn", "reduce the supply"],
    "clause_names": {"A.2.3.1.2.4": "Step 3: Smart Burn Engine", "A.4.1.2.2.3": "Burning Of Existing Tokens"},
    "sky_address": "0x56072c95faa701256059aa122697b133aded9279",
    "atlas_controls": {"Smart Burn Engine": 30, "USDS": 1182},
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


def utc(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def two(x):
    """A non-negative Fraction rounded half up to two decimals, with thousands separators, as the report writes it."""
    cents = int(x * 100 + Fraction(1, 2))
    return f"{cents // 100:,}.{cents % 100:02d}"


def sky2(wei):
    return two(Fraction(wei, WEI))


class Data:
    def __init__(self, snap, big):
        self.snap = snap
        self.calls, self.logs = {}, {}
        for c in snap["calls"]:
            if c["label"] in self.calls:
                raise DataError(f"two calls labelled {c['label']}")
            self.calls[c["label"]] = c
        for q in snap["logs"] + big["logs"]:
            if q["label"] in self.logs:
                raise DataError(f"two log queries labelled {q['label']}")
            self.logs[q["label"]] = evm.unpack(q)

    def word(self, label):
        return int(self.calls[label]["result"], 16)

    def ts(self, block):
        return self.snap["blocks"][str(block)]["timestamp"]


def amount(log):
    return evm.words(log["data"])[0]


def expected_reads(snap):
    """Every call and log query the snapshot must hold: {label: (to, calldata, block)}, {label: (address, topics, from, to)}."""
    a, head = snap["addresses"], snap["pinned_head"]
    aug, sep = snap["end_of_august_block"], snap["end_of_september_block"]
    name_key = lambda n: evm.calldata("getAddress(bytes32)", n.encode().ljust(32, b"\0").hex())
    calls = {f"chainlog {n}": (CHAINLOG, name_key(n), A_END) for n in NAMES}
    calls.update({f"chainlog MCD_FLAP@{b}": (CHAINLOG, name_key("MCD_FLAP"), b) for b in (B_START, SCALE_END)})
    calls.update({f"MCD_FLAP.pair()@{b}": (a["MCD_FLAP"], evm.selector("pair()"), b) for b in (B_START, SCALE_END, CAST, A_END)})
    calls.update({f"SKY.totalSupply()@{b}": (a["SKY"], evm.selector("totalSupply()"), b) for b in (B_START, CAST, A_END, head)})
    calls.update({f"SKY.balanceOf({h})@{b}": (a["SKY"], evm.calldata("balanceOf(address)", evm.word_address(h)[2:]), b)
                  for b in (CAST, A_END) for h in HOLDERS})
    calls["MCD_SPLIT.burn()"] = (a["MCD_SPLIT"], evm.selector("burn()"), A_END)
    w = evm.word_address
    kicks, bought = [T_TRANSFER, w(a["MCD_FLAP"]), w(a["pair"])], [T_TRANSFER, w(a["pair"]), w(a["MCD_PAUSE_PROXY"])]
    burn, mint = [T_TRANSFER, None, ZERO_WORD], [T_TRANSFER, ZERO_WORD, None]
    queries = {
        "SKY Transfer to 0x0, window B": (a["SKY"], burn, B_START, A_END),
        "SKY Transfer from 0x0, window B": (a["SKY"], mint, B_START, A_END),
        "USDS MCD_FLAP -> pair, window A": (a["USDS"], kicks, CAST, A_END),
        "SKY pair -> MCD_PAUSE_PROXY, window A": (a["SKY"], bought, CAST, A_END),
        "USDS MCD_FLAP -> pair, scale range": (a["USDS"], kicks, B_START, SCALE_END),
        "SKY pair -> MCD_PAUSE_PROXY, scale range": (a["SKY"], bought, B_START, SCALE_END),
        "SKY Transfer to 0x0, cast to pinned head": (a["SKY"], burn, CAST, head),
        "SKY Transfer from 0x0, cast to pinned head": (a["SKY"], mint, CAST, head),
        "USDS MCD_FLAP -> pair, cast to end of August": (a["USDS"], kicks, CAST, aug),
        "SKY pair -> MCD_PAUSE_PROXY, cast to end of August": (a["SKY"], bought, CAST, aug),
        "USDS MCD_FLAP -> pair, September": (a["USDS"], kicks, aug + 1, sep),
        "SKY pair -> MCD_PAUSE_PROXY, September": (a["SKY"], bought, aug + 1, sep),
    }
    return calls, queries


def check_structure(d, big):
    """Each call is the call its label names (target, calldata, block), each log query has the filter and range its
    label names, every log satisfies its filter, and the addresses used are the ones the chainlog returned."""
    snap = d.snap
    for n in NAMES:
        if snap["addresses"].get(n) != evm.address_of(d.calls[f"chainlog {n}"]["result"]):
            raise DataError(f"address of {n} is not the chainlog's")
    if snap["addresses"].get("pair") != evm.address_of(d.calls[f"MCD_FLAP.pair()@{CAST}"]["result"]):
        raise DataError("pair is not MCD_FLAP.pair() at the cast")
    calls, queries = expected_reads(snap)
    if set(calls) != set(d.calls) or set(queries) != set(d.logs):
        raise DataError(f"reads differ from the expected set: {sorted(set(calls) ^ set(d.calls) | set(queries) ^ set(d.logs))[:5]}")
    for label, (to, data, block) in calls.items():
        c = d.calls[label]
        if (c["to"], c["data"], c["block"]) != (to, data, block):
            raise DataError(f"{label} is not the call its label names")
        if str(block) not in snap["blocks"]:
            raise DataError(f"no header for block {block}")
    for q in snap["logs"] + big["logs"]:
        want = queries[q["label"]]
        if (q["address"], q["topics"], q["from"], q["to"]) != (want[0], want[1], want[2], want[3]):
            raise DataError(f"{q['label']} is not the query its label names")
        for l in evm.unpack(q):
            if not evm.matches_filter(l, q["address"], q["topics"]) or not q["from"] <= l["blockNumber"] <= q["to"]:
                raise DataError(f"{q['label']}: a log outside its filter")


def windows_within(amounts, target, tolerance):
    """Contiguous runs amounts[i:j] whose total × 10/55 is within tolerance of target (all in wei)."""
    prefix = [0]
    for x in amounts:
        prefix.append(prefix[-1] + x)
    lo_total, hi_total = (target - tolerance) * 55, (target + tolerance) * 55
    found = []
    for i in range(len(prefix)):
        for j in range(bisect.bisect_left(prefix, prefix[i] + -(-lo_total // 10), i + 1), len(prefix)):
            total = (prefix[j] - prefix[i]) * 10
            if total >= hi_total:
                break
            if lo_total < total:
                found.append((i, j))
    return found


class Rows:
    def __init__(self):
        self.rows = []

    def _add(self, cid, claim, stated, computed, level, status, note):
        self.rows.append({"id": cid, "claim": claim, "stated": stated, "computed": computed,
                          "level": level, "status": status, "note": note})

    def check(self, cid, claim, stated, computed, level, conditions, note=""):
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

    def erratum(self, cid, claim, stated, computed, level, matches_statement, correction, note):
        """correction: named conditions, all of which the documented correction relies on."""
        broken = [name for name, ok in correction.items() if not ok]
        if matches_statement:
            status, note = "FAIL", "the statement now matches; the erratum is wrong. " + note
        elif broken:
            status, note = "FAIL", f"the documented correction no longer holds ({', '.join(broken)}). " + note
        else:
            status = "ERRATUM"
        self._add(cid, claim, stated, computed, level, status, note)


def paired(a, b):
    """Kicks pair one-to-one when each transaction carries exactly one log on each leg."""
    ta, tb = [l["transactionHash"] for l in a], [l["transactionHash"] for l in b]
    return len(set(ta)) == len(ta) and sorted(ta) == sorted(tb)


def results(case=CASE):
    verify_data(case)
    big = json.loads((case / "data" / "window_b_buybacks.json").read_text(encoding="utf-8"))
    d = Data(json.loads((case / "data" / "live_snapshot.json").read_text(encoding="utf-8")), big)
    check_structure(d, big)
    supply = {b: d.word(f"SKY.totalSupply()@{b}") for b in (B_START, CAST, A_END)}
    r = Rows()

    # L: on-chain facts re-read at the blocks the report names.
    a_days = (d.ts(A_END) - d.ts(CAST)) / 86400
    r.check("L1", "window A: blocks 25,775,271 (spell cast) to 25,780,667",
            f"{REPORT['cast_utc']} → {REPORT['a_end_utc']}, {REPORT['a_days']} days",
            f"{utc(d.ts(CAST))} → {utc(d.ts(A_END))}, {a_days:.4f} days", REREAD,
            {"cast time": utc(d.ts(CAST)) == REPORT["cast_utc"], "end time": utc(d.ts(A_END)) == REPORT["a_end_utc"]})
    kicks = d.logs["USDS MCD_FLAP -> pair, window A"]
    bought = d.logs["SKY pair -> MCD_PAUSE_PROXY, window A"]
    usds = Fraction(sum(map(amount, kicks)), WEI)
    r.check("L2", "window A: Smart Burn Engine kicks (USDS, MCD_FLAP → pair)", f"{REPORT['a_kicks']}, ${REPORT['a_usds']:,}",
            f"{len(kicks)}, {float(usds):,.2f} USDS", REREAD,
            {"16 kicks": len(kicks) == REPORT["a_kicks"], "$52,800": usds == REPORT["a_usds"]},
            "The report's dollars are USDS amounts, not a market price.")
    r.check("L3", "window A: SKY received (pair → MCD_PAUSE_PROXY), n matching on both legs",
            f"{REPORT['a_sky']} SKY, {REPORT['a_kicks']} logs", f"{sky2(sum(map(amount, bought)))} SKY, {len(bought)} logs", REREAD,
            {"976,150.62 SKY": sky2(sum(map(amount, bought))) == REPORT["a_sky"], "16 logs": len(bought) == REPORT["a_kicks"],
             "one SKY log per kick transaction": paired(kicks, bought)})
    burns_a = [l for l in d.logs["SKY Transfer to 0x0, window B"] if CAST <= l["blockNumber"] <= A_END]
    r.check("L4", "window A: Transfer on SKY with to = 0x0", REPORT["a_burns"], len(burns_a), REREAD,
            {"none": len(burns_a) == REPORT["a_burns"]})
    r.check("L5", "window A: SKY.totalSupply() at both endpoints", f"{REPORT['supply_a']} at both, 0 wei difference",
            f"{supply[CAST]} / {supply[A_END]}", REREAD,
            {"at the cast": supply[CAST] == REPORT["supply_a"], "at the end": supply[A_END] == REPORT["supply_a"]})
    held = {h: (d.word(f"SKY.balanceOf({h})@{CAST}"), d.word(f"SKY.balanceOf({h})@{A_END}")) for h in HOLDERS}
    r.check("L6", "window A: balances of 0x0, 0x…dEaD and 0x…0001 at both endpoints", "unchanged",
            " · ".join(f"{h[:4]}…{h[-4:]} {a}→{b}" for h, (a, b) in held.items()), REREAD,
            {f"{h[-4:]} unchanged": a == b for h, (a, b) in held.items()})
    b_days = (d.ts(A_END) - d.ts(B_START)) / 86400
    r.check("L7", "window B: blocks 22,950,000 to 25,780,667",
            f"{REPORT['b_start_day']} → {REPORT['b_end_day']}, {REPORT['b_days']} days",
            f"{utc(d.ts(B_START))[:10]} → {utc(d.ts(A_END))[:10]}, {b_days:.2f} days", REREAD,
            {"start day": utc(d.ts(B_START))[:10] == REPORT["b_start_day"], "end day": utc(d.ts(A_END))[:10] == REPORT["b_end_day"],
             "395 days": int(b_days) == REPORT["b_days"]})
    burns_b = d.logs["SKY Transfer to 0x0, window B"]
    mints_b = d.logs["SKY Transfer from 0x0, window B"]
    net = sum(map(amount, mints_b)) - sum(map(amount, burns_b))
    r.check("L8", "window B: every Transfer to / from 0x0, and the net against the endpoint supplies",
            f"{REPORT['b_burns']} burns, {REPORT['b_burned_wei']:,} wei · {REPORT['b_mints']} mints · net −1,002 wei; "
            f"…{REPORT['supply_b_start_tail']} / …{REPORT['supply_b_end_tail']}",
            f"{len(burns_b)} burns, {sum(map(amount, burns_b)):,} wei · {len(mints_b)} mints · net {net:,} wei; "
            f"…{str(supply[B_START])[-6:]} / …{str(supply[A_END])[-6:]}", REREAD,
            {"3 burns": len(burns_b) == REPORT["b_burns"], "1,002 wei": sum(map(amount, burns_b)) == REPORT["b_burned_wei"],
             "no mints": len(mints_b) == REPORT["b_mints"],
             "net equals the endpoint difference": net == supply[A_END] - supply[B_START],
             "start supply": str(supply[B_START]).endswith(REPORT["supply_b_start_tail"])})
    times = sorted(d.ts(l["blockNumber"]) for l in burns_b)
    senders = {evm.address_of(l["topics"][1]) for l in burns_b}
    span = times[-1] - times[0] if times else 0
    r.check("L9", "window B: the three burns: one day, one sender, amounts", f"{REPORT['b_burn_day']}, one sender, 1 / 1 / 1000 wei",
            f"{sorted({utc(t)[:10] for t in times})}, {len(senders)} sender, {' / '.join(str(amount(l)) for l in burns_b)} wei",
            REREAD, {"one day": {utc(t)[:10] for t in times} == {REPORT["b_burn_day"]}, "one sender": len(senders) == 1,
                     "amounts": [amount(l) for l in burns_b] == REPORT["b_burn_amounts"]})
    r.erratum("L10", "window B: the three burns fall within 18 minutes", f"within {REPORT['b_burn_minutes']} minutes",
              f"{span // 60} min {span % 60} s", REREAD, span <= REPORT["b_burn_minutes"] * 60,
              {"first to last is 1,116 s": span == 1116, "three burns": len(times) == REPORT["b_burns"]},
              f"E1: first {utc(times[0]) if times else '-'}, last {utc(times[-1]) if times else '-'}; 18 min 36 s, "
              "so 'within 19 minutes'. Nothing else in the report depends on it.")
    sk = d.logs["USDS MCD_FLAP -> pair, scale range"]
    ss = d.logs["SKY pair -> MCD_PAUSE_PROXY, scale range"]
    r.check("L11", "scale range 22,950,000 → 25,578,353: kicks and SKY bought, matched on both legs",
            f"{REPORT['scale_kicks']:,} kicks, {REPORT['scale_sky']} SKY", f"{len(sk):,} kicks, {sky2(sum(map(amount, ss)))} SKY",
            REREAD, {"8,656 kicks": len(sk) == REPORT["scale_kicks"],
                     "868,204,202.24 SKY": sky2(sum(map(amount, ss))) == REPORT["scale_sky"], "paired by transaction": paired(sk, ss),
                     "same flapper over the range": {evm.address_of(d.calls[f"chainlog MCD_FLAP@{b}"]["result"]) for b in (B_START, SCALE_END)}
                     == {d.snap["addresses"]["MCD_FLAP"]},
                     "same pair over the range": {evm.address_of(d.calls[f"MCD_FLAP.pair()@{b}"]["result"]) for b in (B_START, SCALE_END)}
                     == {d.snap["addresses"]["pair"]}},
            "Pairing by transaction makes the SKY leg 8,656 logs as well. The chainlog's MCD_FLAP and its pair() are the "
            "same at both ends of the range as at the cast; that does not rule out a change and a change back in between.")
    split = Fraction(d.word("MCD_SPLIT.burn()"), WEI)
    r.check("L12", "MCD_SPLIT.burn() at block 25,780,667", REPORT["split_burn"], f"{float(split):g}", REREAD,
            {"0.55": f"{float(split):g}" == REPORT["split_burn"]})

    # A: arithmetic on the rows above.
    implied = two(Fraction(sum(map(amount, bought)), WEI) * Fraction(10, 55))
    r.check("A1", "SKY the Atlas allocation implies for the documented 10% burn (976,150.62 × 10/55)",
            f"{REPORT['falsifier_sky']} SKY", f"{implied} SKY", f"{ARITH} on L3", {"177,481.93": implied == REPORT["falsifier_sky"]})

    # P: published sources the report quotes.
    pub = d.snap["public"]
    sol = pub["sky_sol"]
    r.check("P1", "verified Sky.sol: the only writes to totalSupply are mint (+) and burn (−), each followed by its Transfer",
            "two writes; Transfer(address(0), to) / Transfer(from, address(0))",
            f"{len(sol['totalSupply_writes'])} writes; {' / '.join(sol['next_emit_after_each_write'])}", PUBLIC,
            {"source of the SKY the report names, which the chainlog returns":
                 sol["address"] == REPORT["sky_address"] == d.snap["addresses"]["SKY"],
             "single source file": len(sol["source_files"]) == 1,
             "Sourcify matches the deployed code": sol["sourcify_match"].get("runtimeMatch") in ("match", "exact_match"),
             "two writes": len(sol["totalSupply_writes"]) == 2,
             "mint adds": sol["totalSupply_writes"][0:1] == ["totalSupply = totalSupply + value;"],
             "burn subtracts": [w.replace(" ", "") for w in sol["totalSupply_writes"][1:2]] == ["totalSupply=totalSupply-value;"],
             "mint emits from 0x0": sol["next_emit_after_each_write"][0:1] == ["emit Transfer(address(0), to, value);"],
             "burn emits to 0x0": sol["next_emit_after_each_write"][1:2] == ["emit Transfer(from, address(0), value);"]},
            f"Source from Sourcify for the deployed address: {sol['source_files']}, runtime match "
            f"'{sol['sourcify_match'].get('runtimeMatch')}'.")
    at = pub["atlas_at_report"]
    r.check("P2", "the two [Core] clauses, in the Atlas the day the report was posted",
            "A.2.3.1.2.4 - Step 3: Smart Burn Engine [Core]: 45% bought back for staking rewards, 10% '… are burned.' · "
            "A.4.1.2.2.3 - Burning Of Existing Tokens [Core]: "
            "'At the same time that no new SKY tokens are issued … bought back and burned on a regular basis … continue to decrease', "
            "restating A.2.3.1.2.4",
            " · ".join(f"{no}: {c['document_number']} - {c['name']} [{c['type']}] {c['quotes_present']} refers to {c['refers_to']}"
                       for no, c in at["clauses"].items()), PUBLIC,
            {**{f"{no} found by its UUID": c["document_number"] == no for no, c in at["clauses"].items()},
             **{f"{no} name": c["name"] == REPORT["clause_names"][no] for no, c in at["clauses"].items()},
             **{f"{no} is [Core]": c["type"] == "Core" for no, c in at["clauses"].items()},
             **{f"{no} wording": all(c["quotes_present"]) and c["quotes_present"] != [] for no, c in at["clauses"].items()},
             "A.4.1.2.2.3 refers to A.2.3.1.2.4": "A.2.3.1.2.4" in at["clauses"]["A.4.1.2.2.3"]["refers_to"]},
            f"next-gen-atlas at {at['commit'][:10]} ({at['committed_at']}), the last commit before the report.")
    absent = at["absent_phrase_counts"]
    r.check("P3", "searching that Atlas for the implementation note's wording", "0 matches for each of the four phrases",
            ", ".join(f"'{p}' {n}" for p, n in absent.items()), PUBLIC,
            {**{f"'{p}' absent": absent.get(p) == 0 for p in REPORT["atlas_absent"]},
             "the search sees text": all(n > 0 for n in at["present_phrase_counts"].values())},
            f"Positive control on the same files: {at['present_phrase_counts']}. The report's own control counts are not reproduced (see Limits).")
    jul = pub["atlas_mid_july"]["clauses"]["A.4.1.2.2.3"]
    r.check("P5", "the A.4.1.2.2.3 wording was already there in mid-July (the report checked it back to a 2026-07-15 snapshot)",
            "same wording", f"{jul['document_number']} {jul['quotes_present']}", PUBLIC,
            {"found by its UUID": jul["document_number"] == "A.4.1.2.2.3",
             "wording": all(jul["quotes_present"]) and jul["quotes_present"] != []},
            f"next-gen-atlas at {pub['atlas_mid_july']['commit'][:10]} ({pub['atlas_mid_july']['committed_at']}), "
            "the last commit before 2026-07-16.")
    r.record("R1", "the positive control for the Atlas search", ", ".join(f"'{k}' {v:,}" for k, v in REPORT["atlas_controls"].items()),
             "not re-derived", "Recorded (report text)",
             f"Counted on a privately assembled Atlas snapshot that is not kept. The repository changed from one file per clause "
             f"to {at['content_files']} files in the summer of 2026, and counts depend on how clauses are joined; on the "
             f"source files at {at['commit'][:10]} the phrases occur {at['present_phrase_counts']}. P3's zero matches do not depend on this.")
    forum = pub["forum_28153_post_1"]
    r.check("P4", "the TMF implementation note (forum topic 28153): no explicit burn; the 10% is implemented through non-issuance",
            "both sentences", f"{forum['author']} {forum['created_at'][:10]}: {forum['quotes_present']}", PUBLIC,
            {"no explicit burn": forum["quotes_present"][:1] == [True], "non-issuance": forum["quotes_present"][1:] == [True]})

    # O: after the report. Observations, not statements of the report.
    reply, issue = pub["maintainer_reply"], pub["issue_306"]
    r.observe("O1", "the maintainer's reply, closing the issue", "burned in a future executive vote; no Atlas change",
              f"{reply['author']} ({reply['association']}) {reply['created_at']}: {reply['says_burned_in_future_executive_vote']}; "
              f"issue {issue['state']} {issue['closed_at']}", PUBLIC,
              {"reply says so": reply["says_burned_in_future_executive_vote"], "reply says no Atlas change": reply["says_no_atlas_change"],
               "closed": issue["state"] == "closed",
               "closed with the reply": issue["closed_at"] == reply["created_at"]})
    head = d.snap["pinned_head"]
    burns_after = d.logs["SKY Transfer to 0x0, cast to pinned head"]
    mints_after = d.logs["SKY Transfer from 0x0, cast to pinned head"]
    now = d.word(f"SKY.totalSupply()@{head}")
    listed = [f"{utc(d.ts(l['blockNumber']))[:10]} {sky2(amount(l))} SKY from {evm.address_of(l['topics'][1])[:10]}…" for l in burns_after]
    r.observe("O2", f"every Transfer to / from 0x0 since the cast, to block {head:,}", "two burns from MCD_PAUSE_PROXY, no mints; supply closes",
              f"{'; '.join(listed)}; {len(mints_after)} mints; totalSupply {sky2(supply[CAST] - now)} SKY lower", REREAD,
              {"two burns": len(burns_after) == 2, "no mints": len(mints_after) == 0,
               "both from MCD_PAUSE_PROXY": {evm.address_of(l["topics"][1]) for l in burns_after} == {d.snap["addresses"]["MCD_PAUSE_PROXY"]},
               "supply change equals the burns": supply[CAST] - now == sum(map(amount, burns_after)) - sum(map(amount, mints_after))},
              "MCD_PAUSE_PROXY is the contract executive spells act through; that the burns came from spells is inferred from "
              "the sender, the spells' code was not read here.")
    aug, sep = d.snap["end_of_august_block"], d.snap["end_of_september_block"]
    aug_q, sep_q = "SKY pair -> MCD_PAUSE_PROXY, cast to end of August", "SKY pair -> MCD_PAUSE_PROXY, September"
    all_kicks = d.logs[aug_q] + d.logs[sep_q]
    amounts = [amount(l) for l in all_kicks]
    window_a = {l["transactionHash"] for l in bought}
    month_rows = [
        ("O3", "the 2026-09-13 burn against 10/55 of the SKY bought from the cast to the end of August",
         aug_q, "USDS MCD_FLAP -> pair, cast to end of August", aug, 0, (0, len(d.logs[aug_q]))),
        ("O4", "the 2026-10-12 burn against 10/55 of the SKY bought in September",
         sep_q, "USDS MCD_FLAP -> pair, September", sep, 1, (len(d.logs[aug_q]), len(all_kicks))),
    ]
    for cid, claim, sky_q, usds_q, end, i, own in month_rows:
        got = sum(map(amount, d.logs[sky_q]))
        want = Fraction(got, WEI) * Fraction(10, 55)
        burned = amount(burns_after[i]) if len(burns_after) > i else 0
        burn = Fraction(burned, WEI)
        day = utc(d.ts(burns_after[i]["blockNumber"]))[:10] if len(burns_after) > i else None
        runs = windows_within(amounts, burned, WEI)
        others = [w for w in runs if w != own]
        conds = {"burn date": day == BURN_DATES[i], "within 1 SKY": abs(burn - want) < 1,
                 "one SKY log per kick": paired(d.logs[usds_q], d.logs[sky_q]),
                 "window ends at the month's last block": d.ts(end) < d.ts(end + 1) and utc(d.ts(end))[:10] != utc(d.ts(end + 1))[:10],
                 "no other run of kicks matches": others == []}
        if cid == "O3":
            conds["includes window A's 16 kicks"] = window_a <= {l["transactionHash"] for l in d.logs[sky_q]}
        r.observe(cid, claim, f"{BURN_DATES[i]}: equal, within 1 SKY, and no other window matches",
                  f"{day}: burned {float(burn):,.2f}; 10/55 × {float(Fraction(got, WEI)):,.2f} = {float(want):,.2f} "
                  f"({len(d.logs[sky_q])} kicks); difference {float(burn - want):+.4f}; {len(others)} other matching run(s)", REREAD, conds,
                  f"Window ends at block {end:,} ({utc(d.ts(end))}); the next block is {utc(d.ts(end + 1))}. Every contiguous run of "
                  f"the {len(all_kicks):,} kicks from the cast to 30 September was tried; only this one is within 1 SKY."
                  + (" It contains window A, so the 177,481.93 SKY of A1 is inside this burn." if cid == "O3" else ""))
    now_atlas = pub["atlas_now"]
    r.observe("O5", "the two clauses in the Atlas as of 2026-10-22", "number, name, [Core] and the quoted passages as at the report",
              " · ".join(f"{no}: {c['document_number']} - {c['name']} [{c['type']}] {c['quotes_present']}"
                         for no, c in now_atlas["clauses"].items()), PUBLIC,
              {**{f"{no} number": c["document_number"] == no for no, c in now_atlas["clauses"].items()},
               **{f"{no} name": c["name"] == REPORT["clause_names"][no] for no, c in now_atlas["clauses"].items()},
               **{f"{no} [Core]": c["type"] == "Core" for no, c in now_atlas["clauses"].items()},
               **{f"{no} quoted passages": all(c["quotes_present"]) and c["quotes_present"] != [] for no, c in now_atlas["clauses"].items()}},
              f"next-gen-atlas at {now_atlas['commit'][:10]}. Only these passages are checked, not the whole text of each clause.")
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
    except (DataError, KeyError, ValueError, IndexError) as e:
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
              f"{counts['RECORDED']} recorded only; post-report observations: {counts['OBSERVED']}; fail: {counts['FAIL']}")
        print("matches by evidence: " + ", ".join(f"{k}: {v}" for k, v in by_level.items()))
    return 1 if any(x["status"] == "FAIL" for x in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
