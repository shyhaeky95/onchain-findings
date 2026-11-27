#!/usr/bin/env python3
"""Read Hyperliquid's Info API and docs now, and check that they still show the pattern this case records.

Usage:
    python3 live_verify.py --check                 # no key needed
    python3 live_verify.py --check --save DIR      # also keep the responses in DIR as new files (never in data/)
    python3 live_verify.py --check --coingecko     # also print CoinGecko's total_supply (informational)

These public Info queries take no historical block or timestamp, so the 2026-11-26 responses in
data/ cannot be read again through them, and the figures move every day as the Assistance Fund
keeps buying HYPE. This script is a regression check: it reads the current state and checks that
it still has the shape the case relies on, not that the figures are the same.

    G1 again   the genesis allocations in tokenDetails add up to maxSupply, none is the fund's, none
               is negative or repeated, and none goes through another token
    L1–L3      the supply figures are finite and in order (0 <= circulatingSupply <= totalSupply
               <= maxSupply, 0 <= futureEmissions <= totalSupply), maxSupply is 1,000,000,000,
               circulatingSupply = totalSupply − futureEmissions − the non-circulating balances,
               each non-circulating address appears once, none is negative, and the fund is among
               them with a positive balance that the fund's own spot state (token 150) confirms
    snapshot   neither the fund's balance nor maxSupply − totalSupply is below the 2026-11-26
               figure: the second catches a net increase in reported totalSupply, not every
               addition (an addition offset by a larger removal would pass)
    A1 margin  the fund's balance is at least ten times maxSupply − totalSupply. This is a
               conservative alert, not the finding itself: under A1's model it means at least 90%
               of the fund is still counted. It was 37.1 times on 2026-11-26; removing even the
               fund's balance on the day of the vote (about 37.5 million HYPE) would fail it.
    docs       the Fees page, identified by its title, still has the quoted sentence in its
               visible text

--check, in order of precedence:
    exit 2  a read failed, a response was unusable (missing fields, not finite, no single HYPE
            entry for token 150), the saved snapshot failed its manifest, the two reads of the
            fund's balance disagree by more than 1,000 HYPE while everything else holds
            (inconclusive), or --save could not write
    exit 1  a usable response breaks one of the relations above
    exit 3  the relations hold, but the Fees page, identified by its title, no longer has the sentence
    exit 4  the relations hold, but the Fees page could not be read or identified
    exit 0  the relations hold and the sentence is there
The current lower bound under A1's model is printed with the figures. CoinGecko, when asked for,
is printed only and never changes the exit code.
"""
import argparse
import datetime
import importlib.util
import json
import sys
import urllib.request
from decimal import Decimal
from pathlib import Path

CASE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("hyperliquid_af_reproduce", CASE / "reproduce.py")
reproduce = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reproduce)
_spec = importlib.util.spec_from_file_location("hyperliquid_af_extract", CASE / "extract.py")
extract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(extract)

COINGECKO = "https://api.coingecko.com/api/v3/coins/hyperliquid?localization=false&tickers=false&community_data=false&developer_data=false"
REQUESTS = {
    "tokenDetails": {"type": "tokenDetails", "tokenId": reproduce.HYPE_TOKEN_ID},
    "spotClearinghouseState_AF": {"type": "spotClearinghouseState", "user": reproduce.AF},
}
# An operational tolerance, not evidence that the two reads saw the same state: the fund buys HYPE
# continuously (about 0.35 HYPE a second in November 2026), so 1,000 HYPE allows for some minutes
# between the two reads. It is about 0.002% of the fund's balance.
SPOT_DRIFT = Decimal("1000")
MIN_RATIO = 10
DOCS_TITLE = "Fees"
DRIFT = "the two reads of the fund's balance agree within 1,000 HYPE"


def http_post(body):
    req = urllib.request.Request(reproduce.INFO_ENDPOINT, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def http_get(url):
    req = urllib.request.Request(url, headers={"Accept": "*/*", "User-Agent": "onchain-findings live_verify"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def snapshot(case=CASE):
    """The fund's balance and the gap on 2026-11-26, after the same manifest and capture checks as reproduce.py."""
    reproduce.verify_data(case)
    td, _, _ = reproduce.load(case)
    af = reproduce.number(dict(td["nonCirculatingUserBalances"])[reproduce.AF])
    return af, reproduce.number(td["maxSupply"]) - reproduce.number(td["totalSupply"])


def relations(td, spot, before_af, before_gap):
    """The relations on a fresh read. Returns (values, {name: ok}).
    Raises DataError or DecimalException when the response is unusable."""
    n = reproduce.number
    mx, total, circ, fe = (n(td[k]) for k in ("maxSupply", "totalSupply", "circulatingSupply", "futureEmissions"))
    nonc = [(a.lower(), n(b)) for a, b in td["nonCirculatingUserBalances"]]
    genesis = [(a.lower(), n(b)) for a, b in td["genesis"]["userBalances"]]
    hype = [b for b in spot["balances"] if b.get("coin") == "HYPE"]
    if len(hype) != 1 or hype[0].get("token") != reproduce.HYPE_SPOT_INDEX:
        raise reproduce.DataError(f"spot state has no single HYPE entry for token {reproduce.HYPE_SPOT_INDEX}")
    af_spot = n(hype[0]["total"])
    af_rows = [b for a, b in nonc if a == reproduce.AF]
    af = af_rows[0] if len(af_rows) == 1 else None
    gap = mx - total
    values = {"maxSupply": mx, "totalSupply": total, "maxSupply - totalSupply": gap, "circulatingSupply": circ,
              "futureEmissions": fe, "AF balance (non-circulating)": af, "AF balance (spot state)": af_spot,
              "AF balance - gap (A1 bound)": af - gap if af is not None else None}
    ok = {
        "token is HYPE": td.get("name") == "HYPE",
        "genesis allocations add up to maxSupply": abs(sum((b for a, b in genesis), Decimal(0)) - mx) <= reproduce.TOLERANCE,
        "fund not in genesis": all(a != reproduce.AF for a, b in genesis),
        "no genesis allocation through another token": td["genesis"]["existingTokenBalances"] == [],
        "no negative genesis balance": all(b >= 0 for a, b in genesis),
        "each genesis address once": len({a for a, b in genesis}) == len(genesis),
        "0 <= circulatingSupply <= totalSupply <= maxSupply": 0 <= circ <= total <= mx,
        "0 <= futureEmissions <= totalSupply": 0 <= fe <= total,
        "maxSupply is 1,000,000,000": mx == Decimal("1000000000"),
        "circulatingSupply = totalSupply − futureEmissions − non-circulating":
            abs(total - fe - sum((b for a, b in nonc), Decimal(0)) - circ) <= reproduce.TOLERANCE,
        "each non-circulating address once": len({a for a, b in nonc}) == len(nonc),
        "no negative non-circulating balance": all(b >= 0 for a, b in nonc),
        "AF listed once among non-circulating balances, balance > 0": af is not None and af > 0,
        DRIFT: af is not None and abs(af - af_spot) <= SPOT_DRIFT,
        "AF balance not below 2026-11-26": af is not None and af >= before_af,
        "maxSupply − totalSupply not below 2026-11-26 (no net increase in reported totalSupply)": gap >= before_gap,
        f"AF balance at least {MIN_RATIO} × (maxSupply − totalSupply)": af is not None and af >= MIN_RATIO * gap,
    }
    return values, ok


def docs_state(page):
    """'found' or 'missing' for a page identified as the Fees page by its title; 'unidentified' otherwise."""
    if DOCS_TITLE not in extract.page_title(page).split("|")[0]:
        return "unidentified"
    return "found" if reproduce.FINDING["docs_quote"] in extract.page_text(page) else "missing"


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def save(out, raw):
    """Write the responses into out as new files only; refuse data/ and anything that resolves into it.
    Returns an error message or None."""
    data = (CASE / "data").resolve()
    out = Path(out).resolve()
    if out == data or data in out.parents:
        return "refusing to write into data/: the committed responses are the 2026-11-26 snapshot"
    try:
        out.mkdir(parents=True, exist_ok=True)
        for name, b in raw.items():
            target = out / name
            if target.resolve() == data or data in target.resolve().parents:
                return f"refusing to write {name}: it resolves into data/"
            with open(target, "xb") as f:   # never overwrite: an existing name may be a link to another file
                f.write(b)
    except OSError as e:
        return f"save failed: {e}"
    return None


def main(argv=None, post=None, get=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", required=True)
    ap.add_argument("--save", metavar="DIR")
    ap.add_argument("--coingecko", action="store_true")
    args = ap.parse_args(argv)
    post, get = post or http_post, get or http_get
    try:
        before_af, before_gap = snapshot()
        raw, times = {}, {}
        for name, body in REQUESTS.items():
            start = now()
            raw[f"info_{name}.json"] = post(body)
            times[name] = (start, now())
        td = json.loads(raw["info_tokenDetails.json"])
        spot = json.loads(raw["info_spotClearinghouseState_AF.json"])
        values, ok = relations(td, spot, before_af, before_gap)
    except Exception as e:  # nothing was compared: never exit 1 for a failed or unusable read
        print(f"UNUSABLE: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    for name, (a, b) in times.items():
        print(f"{name:<32} read {a} → {b}")
    for k, v in values.items():
        print(f"{k:<32} {reproduce.two(v) if v is not None else 'missing'}")
    af = values["AF balance (non-circulating)"]
    if af is not None:
        print(f"{'AF balance change since 2026-11-26':<32} {af - before_af:+,.2f}")
        if af > 0:
            print(f"{'share of the fund still counted':<32} at least {(af - values['maxSupply - totalSupply']) / af:.2%}"
                  " (under A1's model)")
    state = "unread"
    try:
        page = get(reproduce.DOCS_URL)
        raw["docs_trading_fees.html"] = page
        state = docs_state(page)
    except Exception as e:
        print(f"(docs page not read: {type(e).__name__}: {e})", file=sys.stderr)
    print(f"{'docs sentence':<32} {state}")
    if args.coingecko:
        try:
            cg = json.loads(get(COINGECKO), parse_float=str)
            cg_total = reproduce.number(cg["market_data"]["total_supply"])
            print(f"{'CoinGecko total_supply':<32} {reproduce.two(cg_total)} "
                  f"(API totalSupply − CoinGecko: {reproduce.two(values['totalSupply'] - cg_total)}, "
                  f"last_updated {cg.get('last_updated')})")
        except Exception as e:
            print(f"(CoinGecko not read: {type(e).__name__}: {e})", file=sys.stderr)
    if args.save:
        err = save(args.save, raw)
        if err:
            print(err, file=sys.stderr)
            return 2
    for name, good in ok.items():
        print(f"{'ok  ' if good else 'NO  '} {name}")
    if not ok[DRIFT] and all(v for k, v in ok.items() if k != DRIFT):
        print("only the two reads of the fund's balance disagree: inconclusive", file=sys.stderr)
        return 2
    if not all(ok.values()):
        return 1
    return {"found": 0, "missing": 3, "unidentified": 4, "unread": 4}[state]


if __name__ == "__main__":
    sys.exit(main())
