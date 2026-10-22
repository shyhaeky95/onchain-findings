#!/usr/bin/env python3
"""Offline reproduction of DefiLlama/DefiLlama-Adapters#20659 (Uniswap v4 TVL on BSC).

Usage:
    python3 reproduce.py                  # verify data, recompute, print one row per claim
    python3 reproduce.py --json           # same, as JSON
    python3 reproduce.py --update-hashes  # refresh manifest hashes for recorded/snapshot files only
    python3 reproduce.py --check-extract DIR
                                          # rebuild data/extract.json from the three full DefiLlama
                                          # responses in DIR (checked against MANIFEST.json) and
                                          # compare it byte for byte with the committed file

No network, no API key, standard library only.

Each row compares the report's own wording ("stated", fixed in REPORT below, copied from
the issue text) with what the evidence gives ("computed"). Status:
    MATCH     the evidence supports a statement of the report
    ERRATUM   the statement is wrong, and the evidence equals the documented correction,
              including every fact the correction's note relies on
    RECORDED  a value that exists only as a kept result; it agrees with the report but
              nothing here re-derives it
    OBSERVED  a fact about the sources after the fix; not a statement of the report
    FAIL      anything else, including an erratum whose correction no longer holds
Exit 0 when no row is FAIL; exit 1 on any FAIL or data-integrity error.
"""
import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

CASE = Path(__file__).resolve().parent
ISSUE_BLOCK = "117739591"
RUN_BLOCK = "117733791"
DATES = ("2026-08-22", "2026-08-23", "2026-08-24")

# Figures exactly as written in the issue text. Recorded copies are checked against these,
# so editing recorded.json cannot move what the report is said to have stated.
REPORT = {
    "tvl": dict(zip(DATES, (40118209, 4854351010, 5750137253))),
    "volume": [185751332, 202309984, 198805372],
    "chain_tvl_b": [5.572, 10.355, 11.27],
    "eth_tvl": 639212959,
    "supply": 11976071028505.97,
    "balance": 125410226751.92,
    "pegged_supply": 18132974.10,
    "remaining_holdings": {"USDT": 12374053, "USDC": 1138807, "WBNB": 290.11, "BTCB": 3.57},
    "price": 0.04465620903964393,
    "price_confidence": 0.99,
    "gt_price": 1.774178705e-10,
    "gt_fdv": 2124.77,
    "gt_volume": 1.06,
    "coingecko_sandbox": 0.04474,
    "tokens_nonzero": 72101,
    "eth_basket": 413083022,
    "adapter_sha256": "83b3df4319131e7f722471729103a59761d0e21c0e1f68fb1f5a9a3e142b39fa",
}

EXTRACT = "data/extract.json"
EXTRACTED = "Extract of saved API responses"
ARITH = "Arithmetic"
REREAD = "Re-read at block, 2026-10-21 snapshot"
PUBLIC = "Public sources as of 2026-10-21 (snapshot)"
RECORDED = "Recorded"

# Written at the top of data/extract.json. The extract keeps only the figures the report uses.
ABOUT = {
    "source": "DefiLlama, https://defillama.com",
    "what": "The figures used in DefiLlama-Adapters#20659, with their timestamps, copied from three DefiLlama "
            "API responses served on 2026-08-24. The complete responses are not included.",
    "corrected": "DefiLlama has since corrected this history. On 2026-10-21 the protocol endpoint served about $40M a "
                 "day of uniswap-v4 TVL on BSC for 2026-08-22 to 08-24 (row O3); the volume and chain-TVL endpoints "
                 "were not re-checked, nor was R7.",
    "permission": "Included with DefiLlama's permission, given by email on 2026-10-21, for an extract of the "
                  "figures used in the report, credited and linked.",
}


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


def load(case, rel):
    return json.loads((case / rel).read_text(encoding="utf-8"))


def day(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d")


def by_day(points, ts_key, value):
    """Map UTC day -> value; two points on one day would make the mapping ambiguous."""
    out = {}
    for p in points:
        d = day(p[ts_key])
        if d in out:
            raise DataError(f"two data points on {d}")
        out[d] = value(p)
    return out


def tvl_series(protocol, chain):
    return by_day(protocol["chainTvls"][chain]["tvl"], "date", lambda p: p["totalLiquidityUSD"])


def volume_series(dexs, chain):
    rows = [{"ts": ts, "v": m.get(chain)} for ts, m in dexs["totalDataChartBreakdown"] if m.get(chain) is not None]
    return by_day(rows, "ts", lambda r: sum(r["v"].values()) if isinstance(r["v"], dict) else r["v"])


def select(key, response):
    """The points of one full response that the report uses, kept in the response's own structure."""
    if key == "protocol":
        keep = {"Binance": DATES, "Ethereum": ("2026-08-24",)}
        return {"chainTvls": {c: {"tvl": [p for p in response["chainTvls"][c]["tvl"] if day(p["date"]) in days]}
                              for c, days in keep.items()}}
    if key == "dexs":
        return {"totalDataChartBreakdown": [[ts, {"BSC": m["BSC"]}] for ts, m in response["totalDataChartBreakdown"]
                                            if day(ts) in DATES and m.get("BSC") is not None]}
    if key == "chain_tvl":
        return [p for p in response if day(p["date"]) in DATES]
    raise DataError(f"unknown response: {key}")


def build_extract(src, case=CASE):
    """Rebuild data/extract.json from the full responses in src, each checked against MANIFEST.json."""
    entry = next(f for f in load_manifest(case)["files"] if f["path"] == EXTRACT)
    out = dict(ABOUT, responses={})
    for s in entry["derived_from"]:
        p = Path(src) / s["file"]
        if not p.is_file() or p.stat().st_size != s["bytes"] or sha256(p) != s["sha256"]:
            raise DataError(f"not the response listed in MANIFEST.json: {s['file']}")
        out["responses"][s["key"]] = {"request": s["request"],
                                      "points": select(s["key"], json.loads(p.read_text(encoding="utf-8")))}
    return (json.dumps(out, indent=2) + "\n").encode("utf-8")


def near(x, target, rel):
    return abs(x - target) <= rel * abs(target)


def all_near(a, b, rel):
    return set(a) == set(b) and all(near(a[k], b[k], rel) for k in b)


class Rows:
    def __init__(self):
        self.rows = []

    def _add(self, cid, claim, stated, computed, level, status, note):
        self.rows.append({"id": cid, "claim": claim, "stated": stated, "computed": computed,
                          "level": level, "status": status, "note": note})

    def check(self, cid, claim, stated, computed, level, ok, note=""):
        self._add(cid, claim, stated, computed, level, "MATCH" if ok else "FAIL", note)

    def observe(self, cid, claim, stated, computed, level, ok, note=""):
        self._add(cid, claim, stated, computed, level, "OBSERVED" if ok else "FAIL", note)

    def recorded(self, cid, claim, stated, computed, level, consistent, note=""):
        self._add(cid, claim, stated, computed, level, "RECORDED" if consistent else "FAIL", note)

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


def results(case=CASE):
    verify_data(case)
    ext = load(case, EXTRACT)["responses"]
    protocol, dexs = ext["protocol"]["points"], ext["dexs"]["points"]
    chain_tvl = by_day(ext["chain_tvl"]["points"], "date", lambda p: p["tvl"])
    rec = load(case, "data/recorded.json")
    live = load(case, "data/live_snapshot.json")

    bsc = tvl_series(protocol, "Binance")
    eth = tvl_series(protocol, "Ethereum")
    vol = volume_series(dexs, "BSC")
    blk = live["blocks"][ISSUE_BLOCK]
    run_blk = live["blocks"][RUN_BLOCK]
    tok = blk["token"]
    balance = tok["pool_manager_balance"]
    prices = rec["prices"]
    price = prices["coins_llama_at_issue"]["price_usd"]
    gt = prices["geckoterminal_at_run"]
    enum = rec["enumeration"]
    rest = enum["total_priced_usd"] - enum["top_token"]["usd"]
    basket_bsc = rec["major_token_baskets"]["bsc"]["total_usd"]
    eth_basket = rec["major_token_baskets"]["ethereum"]["total_usd"]
    tvl24 = bsc["2026-08-24"]
    r = Rows()

    # R: DefiLlama figures from the extract. Kept values: the full responses are not published, so nothing
    # here re-derives them (for R1-R3 the endpoint now serves the corrected history, O3).
    for i, d in enumerate(DATES, 1):
        r.recorded(f"R{i}", f"uniswap-v4 BSC TVL on {d}", REPORT["tvl"][d], bsc.get(d), EXTRACTED,
                   bsc.get(d) == REPORT["tvl"][d])
    got = [vol.get(d) for d in DATES]
    r.recorded("R4", "uniswap-v4 BSC daily volume, 08-22 / 08-23 / 08-24", REPORT["volume"], got, EXTRACTED,
               got == REPORT["volume"], "Day labels are DefiLlama's; the 08-24 point predates the end of that day (see Limits).")
    got = [round(chain_tvl[d] / 1e9, 3) for d in DATES]
    r.recorded("R5", "historicalChainTvl/BSC in $B, 08-22 / 08-23 / 08-24", REPORT["chain_tvl_b"], got, EXTRACTED,
               got == REPORT["chain_tvl_b"])
    step = bsc["2026-08-23"] / bsc["2026-08-22"]
    span = tvl24 / bsc["2026-08-22"]
    stated_span = REPORT["tvl"]["2026-08-24"] / REPORT["tvl"]["2026-08-22"]
    r.erratum("R6", "TVL increased about 121x over 2026-08-22 -> 08-24", "~121x", round(span, 1), f"{ARITH} on R1-R3",
              near(span, 121, 0.05), {"08-22->08-24 is 143.3x": round(span, 1) == 143.3,
                                      "08-22->08-23 is 121.0x": round(step, 1) == 121.0},
              f"E1: 121x is the 08-22 -> 08-23 step ({step:.1f}x); 08-22 -> 08-24 is {span:.1f}x. "
              f"The report's own TVL figures give the same {stated_span:.1f}x.")
    r.recorded("R7", "uniswap-v4 Ethereum TVL on 2026-08-24", REPORT["eth_tvl"], eth.get("2026-08-24"), EXTRACTED,
               eth.get("2026-08-24") == REPORT["eth_tvl"])

    # L: on-chain facts re-read at the block the report names, and the adapter source.
    r.check("L1", f"name() / symbol() at block {ISSUE_BLOCK}", "SAND / SAND", f"{tok['name']} / {tok['symbol']}",
            REREAD, (tok["name"], tok["symbol"]) == ("SAND", "SAND"))
    r.check("L2", "totalSupply()", REPORT["supply"], round(tok["total_supply"], 2), REREAD,
            round(tok["total_supply"], 2) == REPORT["supply"])
    r.check("L3", "balance held by the v4 PoolManager", REPORT["balance"], round(balance, 2), REREAD,
            round(balance, 2) == REPORT["balance"])
    peg = blk["pegged_sand"]
    r.check("L4", "Binance-Peg SAND name() / totalSupply()", f"The Sandbox / {REPORT['pegged_supply']:.2f}",
            f"{peg['name']} / {peg['total_supply']:.2f}", REREAD,
            peg["name"] == "The Sandbox" and round(peg["total_supply"], 2) == REPORT["pegged_supply"])
    listed = REPORT["remaining_holdings"]
    at = {k: round(v, 2) for k, v in blk["pool_manager_major_balances"].items()}
    at_run = {k: round(v, 2) for k, v in run_blk["pool_manager_major_balances"].items()}
    usdt_gap = at_run["USDT"] - listed["USDT"]
    r.erratum("L5", f"largest remaining holdings, placed at block {ISSUE_BLOCK}", listed, at, REREAD,
              all_near(at, listed, 0.005),
              {"recorded copy equals the report text": rec["remaining_holdings_listed_in_issue"]["amounts"] == listed,
               f"balances at {ISSUE_BLOCK} equal the correction":
                   at == {"USDT": 12232586.47, "USDC": 1101875.64, "WBNB": 366.55, "BTCB": 3.58},
               f"the list does not match block {RUN_BLOCK} either": not all_near(at_run, listed, 0.005),
               f"its USDT is within 1,000 of block {RUN_BLOCK}": abs(usdt_gap) < 1000},
              f"E2: the list comes from a 726-page enumeration read with `latest`, so it almost certainly "
              f"belongs to no single block. It matches neither {ISSUE_BLOCK} ({at}) nor {RUN_BLOCK} ({at_run}). "
              f"Its USDT figure is {usdt_gap:,.0f} below the balance at {RUN_BLOCK}, consistent with the scan "
              f"starting near that block. L1-L4 do hold at {ISSUE_BLOCK}.")
    pre = live["public"]["adapter"]["before_fix"]
    r.check("L6", "adapter on main before the fix: sha256 / BSC blacklist size / token listed",
            "83b3df43... / 5 / no",
            f"{pre['sha256'][:8]}... / {pre['bsc_blacklist_entries']} / {'yes' if pre['token_blacklisted'] else 'no'}",
            PUBLIC, pre["sha256"] == REPORT["adapter_sha256"] and pre["bsc_blacklist_entries"] == 5
            and not pre["token_blacklisted"])
    r.check("L7", "BLOTIX/SAFEMONEY already get the same treatment in that file", "yes",
            pre["ethereum_blacklists_blotix_and_safemoney"], PUBLIC, pre["ethereum_blacklists_blotix_and_safemoney"] is True,
            "Both addresses are in the Ethereum blacklist of the pre-fix adapter.")

    # A: arithmetic on the rows above and on recorded inputs.
    value = balance * price
    r.erratum("A1", f"at block {ISSUE_BLOCK} the token accounts for about $5.63B (opening sentence)", "~$5.63B",
              f"${value / 1e9:.2f}B", f"{ARITH} on L3 and the recorded price", round(value / 1e9, 2) == 5.63,
              {"balance x price is $5.60B": round(value / 1e9, 2) == 5.60,
               "the enumeration gives $5.63B": round(enum["top_token"]["usd"] / 1e9, 2) == 5.63,
               "its implied price matches neither recorded price":
                   all(abs(enum["top_token"]["usd"] / balance - p) / p > 1e-3
                       for p in (price, prices["coins_llama_at_run"]["price_usd"]))},
              f"E4: balance x the report's price is ${value / 1e9:.3f}B, as the report itself says further down. "
              f"$5.63B is the enumeration's figure (implied price {enum['top_token']['usd'] / balance:.6f}), "
              f"which matches neither recorded price ({price:.6f}, {prices['coins_llama_at_run']['price_usd']:.6f}).")
    r.check("A2", "balance x GeckoTerminal price", "~$22", round(balance * gt["price_usd"], 2),
            f"{ARITH} on L3 and the recorded price", round(balance * gt["price_usd"]) == 22)
    ratio = price / gt["price_usd"]
    r.check("A3", "the two prices differ by about 2.5e8x", "~2.5e8", f"{ratio:.3e}", f"{ARITH} on recorded prices",
            round(ratio / 1e8, 1) == 2.5)
    share = enum["total_priced_usd"] / tvl24 * 100
    r.check("A4", "enumerated priced total vs published BSC TVL", "~98.7%", round(share, 2),
            f"{ARITH} on R3 and the recorded enumeration", round(share, 1) == 98.7)
    top = enum["top_token"]["usd"] / enum["total_priced_usd"] * 100
    r.check("A5", "the token's share of the enumerated total", "$5.63B / 99.26%",
            f"${enum['top_token']['usd'] / 1e9:.2f}B / {top:.2f}%", f"{ARITH} on the recorded enumeration",
            round(enum["top_token"]["usd"] / 1e9, 2) == 5.63 and round(top, 2) == 99.26)
    r.erratum("A6", "remaining priced PoolManager total", "order of $17M", f"${rest / 1e6:.1f}M",
              f"{ARITH} on the recorded enumeration", near(rest, 17e6, 0.25),
              {"remainder is $42.0M": round(rest / 1e6, 1) == 42.0,
               "the major-asset basket is $16.7M": round(basket_bsc / 1e6, 1) == 16.7},
              f"E3: the enumeration's own remainder is ${rest / 1e6:.1f}M; ${basket_bsc / 1e6:.1f}M is a separate "
              "read of nine major assets. Both are tens of millions against a published $5.75B.")
    eth_ratio = eth_basket / eth["2026-08-24"]
    r.check("A7", "Ethereum control: $413,083,022 of major-token holdings vs $639,212,959, same order of magnitude",
            "same order of magnitude", f"${int(eth_basket):,} / {eth_ratio:.3f}x",
            f"{ARITH} on the recorded basket and R7", int(eth_basket) == REPORT["eth_basket"] and 0.1 <= eth_ratio <= 10,
            "The report attributes the gap to the unenumerated long tail; that was not measured.")
    rest_ratio = rest / tvl24
    r.erratum("A8", "priced BSC PoolManager holdings excluding the token vs published TVL", "~0.0029x",
              f"{rest_ratio:.4f}x", f"{ARITH} on R3 and the recorded enumeration", round(rest_ratio, 4) == 0.0029,
              {"whole priced remainder is 0.0073x": round(rest_ratio, 4) == 0.0073,
               "the nine-asset basket is 0.0029x": round(basket_bsc / tvl24, 4) == 0.0029,
               "Ethereum control is 0.646x": round(eth_ratio, 3) == 0.646},
              f"E5: the report's 0.0029x is the nine-asset basket (${basket_bsc / 1e6:.1f}M / TVL); over the whole "
              f"priced remainder it is {rest_ratio:.4f}x. Still far below the Ethereum control's {eth_ratio:.3f}x.")
    implied = gt["fdv_usd"] / gt["price_usd"]
    rel = abs(implied - tok["total_supply"]) / tok["total_supply"]
    r.check("A9", "GeckoTerminal FDV / price equals the on-chain totalSupply", "consistent", f"rel. diff {rel:.1e}",
            f"{ARITH} on recorded GeckoTerminal values and L2", rel < 1e-6,
            "Ties the recorded GeckoTerminal numbers to this token; it does not verify the price itself.")

    # C: values that exist only as kept results.
    c1 = prices["coins_llama_at_issue"]
    r.recorded("C1", "coins.llama.fi price for the token at posting time",
               f"{REPORT['price']} (conf {REPORT['price_confidence']})", f"{c1['price_usd']} (conf {c1['confidence']})",
               f"{RECORDED} (report text)", c1["price_usd"] == REPORT["price"] and c1["confidence"] == REPORT["price_confidence"],
               f"An earlier read during the run gave {prices['coins_llama_at_run']['price_usd']:.6f}.")
    r.recorded("C2", "GeckoTerminal price / FDV / 24h volume",
               f"{REPORT['gt_price']} / ${REPORT['gt_fdv']:,.2f} / ${REPORT['gt_volume']:.2f}",
               f"{gt['price_usd']} / ${gt['fdv_usd']:,.2f} / ${gt['volume_24h_usd']:.2f}", f"{RECORDED} (run)",
               gt["price_usd"] == REPORT["gt_price"] and round(gt["fdv_usd"], 2) == REPORT["gt_fdv"]
               and round(gt["volume_24h_usd"], 2) == REPORT["gt_volume"])
    r.recorded("C3", "PoolManager ERC-20s with a non-zero balance", REPORT["tokens_nonzero"], enum["tokens_nonzero"],
               f"{RECORDED} (run; not tied to one block)", enum["tokens_nonzero"] == REPORT["tokens_nonzero"])
    peg_price = prices["coins_llama_pegged_sand_at_run"]
    r.recorded("C4", "coins.llama.fi returns no price for Binance-Peg SAND", "no price",
               "no price" if peg_price["price_usd"] is None else peg_price["price_usd"], f"{RECORDED} (run)",
               peg_price["price_usd"] is None,
               f"Also no price as of 2026-10-21: {json.dumps(live['public']['coins_llama_current']['pegged_sand'])}.")
    r.recorded("C5", "coingecko:the-sandbox at the same moment", REPORT["coingecko_sandbox"],
               prices["coingecko_the_sandbox_at_issue"]["price_usd"], f"{RECORDED} (report text)",
               prices["coingecko_the_sandbox_at_issue"]["price_usd"] == REPORT["coingecko_sandbox"])

    # O: the sources after the fix. Observations, not statements of the report.
    now = live["public"]
    r.observe("O1", "coins.llama.fi price for the token", "no price", json.dumps(now["coins_llama_current"]["token"]),
              PUBLIC, now["coins_llama_current"]["token"] == {"coins": {}})
    fix, pr, issue = now["adapter"]["fix_20690"], now["pr_20690"], now["issue_20659"]
    r.observe("O2", "PR #20690 merged and adds the token to the BSC blacklist", "merged 2026-08-25; listed",
              f"merged {pr['merged_at']}; listed: {fix['token_blacklisted']}; {fix['bsc_blacklist_entries']} entries",
              PUBLIC, (pr["merged_at"] or "").startswith("2026-08-25") and pr["merge_commit_sha"] == fix["commit"]
              and fix["token_blacklisted"] is True and fix["bsc_blacklist_entries"] == 6)
    served = [now["defillama_bsc_tvl_now"][d] for d in DATES]
    # A $30M-$60M band is enough: 08-23 and 08-24 were served as $4.85B and $5.75B on 2026-08-24 (R2, R3).
    r.observe("O3", "published BSC TVL for 08-22 / 08-23 / 08-24, as served later", "revised to about $40M a day",
              served, PUBLIC, all(v is not None and 30e6 <= v <= 60e6 for v in served),
              "R1-R3 keep the figures served on 2026-08-24.")
    r.observe("O4", "issue #20659 closed as completed", "closed 2026-08-25",
              f"{issue['state']} / {issue['state_reason']} / {issue['closed_at']}", PUBLIC,
              issue["state"] == "closed" and issue["state_reason"] == "completed"
              and (issue["closed_at"] or "").startswith("2026-08-25"))
    return r.rows


def summary(rows):
    counts = {s: sum(x["status"] == s for x in rows) for s in ("MATCH", "ERRATUM", "RECORDED", "OBSERVED", "FAIL")}
    by_level = {}
    for x in rows:
        if x["status"] == "MATCH":
            kind = x["level"].split(" on ")[0].split(" (")[0]
            by_level[kind] = by_level.get(kind, 0) + 1
    return counts, by_level


def update_hashes(case=CASE):
    manifest = load_manifest(case)
    for f in manifest["files"]:
        if f["kind"] in ("raw", "extract"):
            continue
        p = case / f["path"]
        f["sha256"], f["bytes"] = sha256(p), p.stat().st_size
    (case / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    verify_data(case)


def main(argv=None, case=CASE):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--update-hashes", action="store_true")
    ap.add_argument("--check-extract", metavar="DIR")
    args = ap.parse_args(argv)
    try:
        if args.check_extract:
            same = build_extract(args.check_extract, case) == (case / EXTRACT).read_bytes()
            print(f"{EXTRACT} {'is identical to' if same else 'DIFFERS from'} the extract rebuilt from {args.check_extract}")
            return 0 if same else 1
        if args.update_hashes:
            update_hashes(case)
            print("manifest hashes updated for recorded/snapshot files")
            return 0
        rows = results(case)
    except DataError as e:
        print(f"DATA ERROR: {e}", file=sys.stderr)
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
