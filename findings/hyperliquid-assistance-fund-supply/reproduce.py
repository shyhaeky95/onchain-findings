#!/usr/bin/env python3
"""Offline reproduction: the Hyperliquid Assistance Fund's HYPE, governance burn and the Info API's totalSupply.

Usage:
    python3 reproduce.py                  # verify data, recompute, print one row per claim
    python3 reproduce.py --json           # same, as JSON
    python3 reproduce.py --update-hashes  # refresh manifest hashes after extract.py rewrites data/

No network, no API key, standard library only.

Each row compares the case's own wording ("stated", fixed in FINDING below and repeated in
README.md) with what the evidence gives ("computed"). Status:
    MATCH     the evidence in data/ supports the statement, for every condition the row lists
    RECORDED  the statement rests on a source that is not stored here (quotations of posts and
              pages, an extract of a CoinGecko response); the conditions check only that the
              capture record says what the case says. Not counted as a match
    FAIL      anything else
Exit 0 when no row is FAIL; exit 1 on any FAIL or data-integrity error.
"""
import argparse
import datetime
import gzip
import hashlib
import json
import sys
import zlib
from decimal import Decimal, DecimalException
from pathlib import Path

CASE = Path(__file__).resolve().parent

AF = "0x" + "fe" * 20                       # the Assistance Fund system address
ZERO = "0x" + "00" * 20
DEAD = "0x000000000000000000000000000000000000dead"
HYPE_TOKEN_ID = "0x0d01dc56dcaaca66ad901c959b4011ec"
INFO_ENDPOINT = "https://api.hyperliquid.xyz/info"
DOCS_URL = "https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees"
HIP1_URL = "https://hyperliquid.gitbook.io/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-1-native-token-standard"
HYPE_SPOT_INDEX = 150
TOKEN_DETAILS = "data/info_tokenDetails_HYPE.json.gz"
SPOT_AF = "data/info_spotClearinghouseState_AF.json"
PUBLIC = "data/public_sources.json"
DATA_FILES = (TOKEN_DETAILS, SPOT_AF, PUBLIC)
PROPOSAL, RESULT = "2001127850754367525", "2003684257949188396"
ACCOUNT = "HyperFND"
CAPTURED = "2026-11-26T09:56:10Z"
# The circulating identity holds to about 1e-7 HYPE: the API's figures near 1e9 carry the error of
# binary floating point in their last digits. 1e-6 HYPE allows for that and nothing larger.
TOLERANCE = Decimal("0.000001")
TWITTER_EPOCH_MS = 1288834974657

# Figures and quotations exactly as the case states them (README.md).
FINDING = {
    "proposal_utc": "2025-12-17T03:10:33Z",
    "proposal_quote": "The Hyper Foundation is proposing a validator vote to formally recognize the Assistance Fund HYPE "
                      "as burned, removing the tokens permanently from the circulating and total supply.",
    "no_onchain_quote": "No onchain action is required, as the tokens are already in a system address with no private key.",
    "context_quotes": [
        "By voting “Yes,” validators agree to treat the Assistance Fund HYPE as burned.",
        "This vote is binding social consensus to never authorize a protocol upgrade to access this address.",
        "Similar to the zero address, the Assistance Fund system address has never had a private key with control "
        "over its funds.",
    ],
    "result_utc": "2025-12-24T04:28:48Z",
    "result_quote": "HYPE in the Assistance Fund system address of 0xfefefefefefefefefefefefefefefefefefefefe "
                    "has been formally recognized as burned.",
    "vote_quote": "The governance vote was based on stake-weighted consensus, with 85% of stake voting for burning, "
                  "7% against, and 8% abstaining.",
    "docs_quote": "HYPE in the assistance fund is burned, removing the tokens permanently from the circulating and "
                  "total supply.",
    "docs_utc": "2026-11-27T05:51:13Z",
    "hip1_quotes": [
        "maxSupply : the maximum and initial supply.",
        "The supply may decrease over time due to spot order book fees or future burn mechanisms.",
    ],
    "hip1_utc": "2026-11-27T06:45:04Z",
    "genesis_total": "1,000,000,000.00",
    "genesis_entries": 94023,
    "max_supply": "1,000,000,000.00",
    "total_supply": "998,670,601.38",
    "gap": "1,329,398.62",
    "af_balance": "49,345,806.71",
    "af_ratio": "37.1",
    "af_still_counted": "48,016,408.09",
    "circulating": "301,336,596.58",
    "future_emissions": "409,611,423.61",
    "non_circulating_count": 4,
    "zero_balance": "1,673.79",
    "dead_balance": "2.72",
    "cg_total": "949,784,555.37",
    "cg_gap": "48,886,046.01",
    "cg_gap_below_af": "459,760.71",
    "cg_lag_seconds": 76,
}

REPRODUCED = "Reproduced (saved Info API responses, 2026-11-26 09:56:10 UTC)"
X_POSTS = "Public sources as of 2026-11-26 (snapshot)"
DOCS = "Public sources as of 2026-11-27 (snapshot)"
LAG_FORMATS = ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ")
EXTRACT = "Extract (CoinGecko, 2026-11-26)"
ARITH = "Arithmetic"


class DataError(Exception):
    """Inputs are missing, unlisted, inconsistent, or differ from the manifest."""


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(case):
    return json.loads((case / "MANIFEST.json").read_text(encoding="utf-8"))


def verify_data(case):
    manifest = load_manifest(case)
    paths = [f["path"] for f in manifest["files"]]
    if sorted(paths) != sorted(DATA_FILES):
        raise DataError(f"MANIFEST.json must list exactly {sorted(DATA_FILES)}, once each")
    present = {p.relative_to(case).as_posix() for p in (case / "data").rglob("*") if p.is_file()}
    if present - set(paths):
        raise DataError(f"files not in MANIFEST.json: {sorted(present - set(paths))}")
    for f in manifest["files"]:
        p = case / f["path"]
        if not p.is_file():
            raise DataError(f"missing: {f['path']}")
        if p.stat().st_size != f["bytes"] or sha256(p) != f["sha256"]:
            raise DataError(f"checksum mismatch: {f['path']}")
    return manifest


def two(x):
    """A finite Decimal rounded half up to two decimals, with thousands separators."""
    q = Decimal(x).quantize(Decimal("0.01"), rounding="ROUND_HALF_UP")
    return f"{q:,.2f}"


def iso(text):
    """Both X readers' timestamp formats, as UTC ISO 8601 to the second."""
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%a %b %d %H:%M:%S %z %Y"):
        try:
            t = datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
        if t.tzinfo is not None:
            t = t.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return t.strftime("%Y-%m-%dT%H:%M:%SZ")
    raise DataError(f"unreadable timestamp: {text}")


def id_time(pid):
    """An X post's creation time, from the timestamp bits of its ID (to the millisecond)."""
    ms = (int(pid) >> 22) + TWITTER_EPOCH_MS
    t = datetime.datetime(1970, 1, 1) + datetime.timedelta(milliseconds=ms)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def instant(text):
    """A UTC timestamp with its fractional seconds kept."""
    for fmt in LAG_FORMATS:
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise DataError(f"unreadable timestamp: {text}")


def page(public, label):
    hits = [d for d in public["docs"] if d["label"] == label]
    if len(hits) != 1:
        raise DataError(f"expected one docs page labelled {label}, found {len(hits)}")
    return hits[0]


def quote_found(doc, text):
    return any(q["text"] == text and q["found"] is True for q in doc["quotes"])


def number(x):
    d = Decimal(x)
    if not d.is_finite():
        raise DataError(f"not a finite number: {x}")
    return d


def load(case):
    raw = gzip.decompress((case / TOKEN_DETAILS).read_bytes())
    public = json.loads((case / PUBLIC).read_text(encoding="utf-8"))
    spot_raw = (case / SPOT_AF).read_bytes()
    info = public["info"]
    # The saved responses must carry the SHA-256, request and time recorded at capture (by the same author).
    sums = info["response_sha256"]
    if hashlib.sha256(raw).hexdigest() != sums[TOKEN_DETAILS] or hashlib.sha256(spot_raw).hexdigest() != sums[SPOT_AF]:
        raise DataError("an Info response differs from the SHA-256 recorded at capture")
    if info["endpoint"] != INFO_ENDPOINT:
        raise DataError("unexpected Info API endpoint")
    if info["requests"].get(TOKEN_DETAILS) != {"type": "tokenDetails", "tokenId": HYPE_TOKEN_ID}:
        raise DataError("tokenDetails was not requested for the HYPE token id")
    if info["requests"].get(SPOT_AF) != {"type": "spotClearinghouseState", "user": AF}:
        raise DataError("spotClearinghouseState was not requested for the Assistance Fund address")
    if info["captured_utc"] != CAPTURED or public["captured_utc"]["info"] != CAPTURED:
        raise DataError("capture time differs from the one this case reports")
    td = json.loads(raw)
    spot = json.loads(spot_raw)
    if td.get("name") != "HYPE":
        raise DataError("tokenDetails is not for HYPE")
    return td, spot, public


def post(public, pid):
    hits = [p for p in public["x_posts"] if p["id"] == pid]
    if len(hits) != 1:
        raise DataError(f"expected one entry for post {pid}, found {len(hits)}")
    return hits[0]


def found(p, text, reader):
    hits = [q for q in p["quotes"] if q["text"] == text]
    return len(hits) == 1 and hits[0]["found_by"].get(reader) is True


class Rows:
    def __init__(self):
        self.rows = []

    def _add(self, cid, claim, stated, computed, level, status, note):
        self.rows.append({"id": cid, "claim": claim, "stated": stated, "computed": computed,
                          "level": level, "status": status, "note": note})

    @staticmethod
    def _broken(conditions, note):
        broken = [name for name, ok in conditions.items() if not ok]
        return broken, (f"does not hold: {', '.join(broken)}. " + note if broken else note)

    def check(self, cid, claim, stated, computed, level, conditions, note=""):
        broken, note = self._broken(conditions, note)
        self._add(cid, claim, stated, computed, level, "FAIL" if broken else "MATCH", note)

    def record(self, cid, claim, stated, computed, level, conditions, note=""):
        broken, note = self._broken(conditions, note)
        self._add(cid, claim, stated, computed, level, "FAIL" if broken else "RECORDED", note)


def post_meta(p, utc):
    r = p["readers"]
    return {
        f"syndication time {utc}": iso(r["syndication"]["created_at"]) == utc,
        f"fxtwitter time {utc}": iso(r["fxtwitter"]["created_at"]) == utc,
        f"account @{ACCOUNT} (both readers)": r["syndication"]["account"] == ACCOUNT == r["fxtwitter"]["account"],
        f"link to @{ACCOUNT}": p["url"] == f"https://x.com/{ACCOUNT}/status/{p['id']}",
    }


def balances_of(nonc, address):
    return [number(b) for a, b in nonc if a.lower() == address]


def results(case=CASE):
    verify_data(case)
    td, spot, public = load(case)
    r = Rows()

    # P, R: what the Hyper Foundation and the Hyperliquid docs say. Quotations are checked against the
    # capture record only (the posts and the page are not stored), so these rows are RECORDED.
    prop, res = post(public, PROPOSAL), post(public, RESULT)
    cond = post_meta(prop, FINDING["proposal_utc"])
    cond["sentence 1 in the full text (fxtwitter)"] = found(prop, FINDING["proposal_quote"], "fxtwitter")
    cond["sentence 1 in the short text (syndication)"] = found(prop, FINDING["proposal_quote"], "syndication")
    cond["sentence 2 in the full text (fxtwitter)"] = found(prop, FINDING["no_onchain_quote"], "fxtwitter")
    r.record("P1", "Hyper Foundation proposal on X: the vote would remove the AF tokens from circulating and total "
             "supply; no onchain action", f"{FINDING['proposal_utc']}: \"…{FINDING['proposal_quote'][-75:]}\" · "
             f"\"{FINDING['no_onchain_quote'][:30]}…\"",
             f"recorded as found at capture; time from the post ID {id_time(PROPOSAL)}", X_POSTS, cond,
             "The syndication reader returns only the first 279 characters of this post, so sentence 2 is recorded "
             "in the full text only.")
    q1, q2, q3 = FINDING["context_quotes"]
    r.record("P2", "same post: what the vote is, in the Foundation's words (a commitment not to authorize an upgrade "
             "that reaches the fund's address)",
             f"\"{q1}\" · \"{q2[:45]}…\" · \"{q3[:40]}…\"", "recorded as found at capture (full text)", X_POSTS,
             {"treat as burned (fxtwitter)": found(prop, q1, "fxtwitter"),
              "never authorize an upgrade (fxtwitter)": found(prop, q2, "fxtwitter"),
              "never had a private key with control over its funds (fxtwitter)": found(prop, q3, "fxtwitter")},
             "The Foundation's statement about the key is not checked here.")
    cond = post_meta(res, FINDING["result_utc"])
    cond["quote, syndication"] = found(res, FINDING["result_quote"], "syndication")
    cond["quote, fxtwitter"] = found(res, FINDING["result_quote"], "fxtwitter")
    r.record("P3", "Hyper Foundation result on X: AF HYPE formally recognized as burned",
             f"{FINDING['result_utc']}: \"…formally recognized as burned.\"",
             f"recorded as found at capture; time from the post ID {id_time(RESULT)}", X_POSTS, cond)
    docs = page(public, "fees")
    r.record("P4", "Hyperliquid docs, Trading > Fees: AF HYPE burned, removed from circulating and total supply",
             f"{FINDING['docs_utc']}: \"{FINDING['docs_quote']}\"", "recorded as found at capture", DOCS,
             {"page": docs["url"] == DOCS_URL, "capture time": docs["captured_utc"] == FINDING["docs_utc"],
              "quote found": quote_found(docs, FINDING["docs_quote"])},
             "Read a day after the Info responses, in the page's visible text. live_verify.py --check reads it again.")
    hip1 = page(public, "hip1")
    h1, h2 = FINDING["hip1_quotes"]
    r.record("P5", "Hyperliquid docs, HIP-1: maxSupply is the maximum and initial supply; the supply may decrease over time",
             f"{FINDING['hip1_utc']}: \"{h1}\" · \"{h2}\"", "recorded as found at capture", DOCS,
             {"page": hip1["url"] == HIP1_URL, "capture time": hip1["captured_utc"] == FINDING["hip1_utc"],
              "maxSupply is the initial supply": quote_found(hip1, h1), "supply may decrease": quote_found(hip1, h2)},
             "HIP-1 defines the fields of spot tokens; HYPE's tokenDetails uses the same field names. This is the "
             "accounting model A1 assumes; the docs do not describe how the Info API computes totalSupply.")
    r.record("R1", "vote shares: 85% for, 7% against, 8% abstaining (stake-weighted)", "85 / 7 / 8",
             "as the Foundation wrote them", X_POSTS,
             {"quote, syndication": found(res, FINDING["vote_quote"], "syndication"),
              "quote, fxtwitter": found(res, FINDING["vote_quote"], "fxtwitter")},
             "Not re-derived: this case has no verifiable snapshot of validator stake at 2025-12-24 04:00 UTC, and "
             "the forum replies were not counted here.")

    # G, L: what Hyperliquid's Info API returned on 2026-11-26.
    genesis = td["genesis"]["userBalances"]
    g_total = sum((number(b) for a, b in genesis), Decimal(0))
    r.check("G1", "tokenDetails genesis balances: their total, and whether the fund is among them",
            f"{FINDING['genesis_entries']:,} balances, total {FINDING['genesis_total']} = maxSupply; the AF address "
            f"not among them; no genesis allocation through another token",
            f"{len(genesis):,} balances, total {two(g_total)}; {len(td['genesis']['existingTokenBalances'])} through "
            f"other tokens", REPRODUCED,
            {"total": two(g_total) == FINDING["genesis_total"],
             "total equals maxSupply (to 1e-6)": abs(g_total - number(td["maxSupply"])) <= TOLERANCE,
             "number of balances": len(genesis) == FINDING["genesis_entries"],
             "AF address not in genesis": all(a.lower() != AF for a, b in genesis),
             "no genesis allocation through another token": td["genesis"]["existingTokenBalances"] == [],
             "no negative genesis balance": all(number(b) >= 0 for a, b in genesis),
             "each genesis address once": len({a.lower() for a, b in genesis}) == len(genesis)},
            "The reported genesis allocations add up to the reported maxSupply and give the fund nothing, so the "
            "fund's HYPE reached it after genesis. A1 rests on this.")
    mx, total, circ, fe = (number(td[k]) for k in ("maxSupply", "totalSupply", "circulatingSupply", "futureEmissions"))
    gap = mx - total
    r.check("L1", "Info tokenDetails: maxSupply, totalSupply and the difference between them",
            f"{FINDING['max_supply']} · {FINDING['total_supply']} · {FINDING['gap']}",
            f"{two(mx)} · {two(total)} · {two(gap)}", REPRODUCED,
            {"maxSupply": two(mx) == FINDING["max_supply"], "totalSupply": two(total) == FINDING["total_supply"],
             "maxSupply − totalSupply": two(gap) == FINDING["gap"]})
    nonc = td["nonCirculatingUserBalances"]
    af_rows = balances_of(nonc, AF)
    af = af_rows[0] if af_rows else Decimal(-1)
    hype = [b for b in spot["balances"] if b.get("coin") == "HYPE"]
    af_spot = number(hype[0]["total"]) if len(hype) == 1 else Decimal(-1)
    token_index = hype[0].get("token") if len(hype) == 1 else None
    r.check("L2", "AF balance: listed as a non-circulating balance; the account's own spot state agrees",
            FINDING["af_balance"], f"{two(af)} (non-circulating) · {two(af_spot)} (spot state)", REPRODUCED,
            {"AF listed once among non-circulating balances": len(af_rows) == 1,
             "balance": two(af) == FINDING["af_balance"],
             "spot state equals the listed balance to the last decimal": af == af_spot,
             f"spot entry is token {HYPE_SPOT_INDEX} (HYPE)": token_index == HYPE_SPOT_INDEX})
    others = sum((number(b) for a, b in nonc), Decimal(0))
    identity = total - fe - others
    r.check("L3", "circulatingSupply = totalSupply − futureEmissions − the non-circulating balances, the AF among them",
            f"{FINDING['circulating']}; futureEmissions {FINDING['future_emissions']}; "
            f"{FINDING['non_circulating_count']} balances, the AF one of them",
            f"{two(identity)} computed · {two(circ)} returned; futureEmissions {two(fe)}; {len(nonc)} balances",
            REPRODUCED,
            {"identity holds": abs(identity - circ) <= TOLERANCE, "circulatingSupply": two(circ) == FINDING["circulating"],
             "futureEmissions": two(fe) == FINDING["future_emissions"],
             "four non-circulating balances": len(nonc) == FINDING["non_circulating_count"],
             "AF among the subtracted balances": len(af_rows) >= 1},
            "When the fund was first listed as non-circulating is not checked here.")
    zero, dead = balances_of(nonc, ZERO), balances_of(nonc, DEAD)
    r.check("L4", "the zero address and 0x…dead are non-circulating balances too, like the fund",
            f"0x0…0 {FINDING['zero_balance']} · 0x…dead {FINDING['dead_balance']}",
            f"0x0…0 {two(zero[0]) if len(zero) == 1 else 'not listed once'} · "
            f"0x…dead {two(dead[0]) if len(dead) == 1 else 'not listed once'}", REPRODUCED,
            {"zero address listed once": len(zero) == 1 and two(zero[0]) == FINDING["zero_balance"],
             "0x…dead listed once": len(dead) == 1 and two(dead[0]) == FINDING["dead_balance"]},
            "The API leaves HYPE held at these two addresses out of circulatingSupply in the same way. Whether it is "
            "inside totalSupply cannot be told from these figures: together they are smaller than "
            "maxSupply − totalSupply.")

    # A: the comparison the finding rests on.
    r.check("A1", "AF balance against maxSupply − totalSupply, given G1",
            f"{FINDING['af_balance']} against {FINDING['gap']} ({FINDING['af_ratio']}×): at least "
            f"{FINDING['af_still_counted']} of the fund's HYPE is still counted in totalSupply",
            f"{two(af)} against {two(gap)}" + (f" ({af / gap:.1f}×)" if gap > 0 else "") + f"; {two(af - gap)}", ARITH,
            {"AF balance": two(af) == FINDING["af_balance"], "maxSupply − totalSupply": two(gap) == FINDING["gap"],
             "AF balance − (maxSupply − totalSupply)": two(af - gap) == FINDING["af_still_counted"]},
            "A bound under an accounting model, not a fact about the implementation: if totalSupply is the genesis "
            "total (G1, = maxSupply, as HIP-1 defines it, P5) less what has been taken out of it, and nothing has been "
            "added since, then at most maxSupply − totalSupply HYPE has been taken out, from any cause, and at least "
            "the rest of the fund's balance (97.31% of it) is still counted. The ratio follows from the two figures.")

    # X: CoinGecko's total supply, from an extract of one response.
    cg = public["coingecko"]["fields"]
    cg_total = number(cg["market_data.total_supply"])
    cg_gap = total - cg_total
    lag = (instant(CAPTURED) - instant(cg["last_updated"])).total_seconds()
    r.record("X1", "CoinGecko total_supply against the API's totalSupply",
             f"{FINDING['cg_total']}; {FINDING['cg_gap']} below the API; that difference is "
             f"{FINDING['cg_gap_below_af']} smaller than the AF balance; last_updated {FINDING['cg_lag_seconds']} s "
             f"before the Info read",
             f"{two(cg_total)}; {two(cg_gap)}; {two(af - cg_gap)}; {lag:g} s", EXTRACT,
             {"CoinGecko total_supply": two(cg_total) == FINDING["cg_total"],
              "difference to the API": two(cg_gap) == FINDING["cg_gap"],
              "difference to the AF balance": two(af - cg_gap) == FINDING["cg_gap_below_af"],
              "last_updated lag": lag == FINDING["cg_lag_seconds"],
              "response not published": public["coingecko"]["response_published"] is False},
             "Shown as RECORDED: the CoinGecko response is not published, so nothing here re-derives its figure. "
             "How CoinGecko derives total_supply is not checked here.")
    return r.rows


def summary(rows):
    counts = {k: sum(x["status"] == k for x in rows) for k in ("MATCH", "RECORDED", "FAIL")}
    by_level = {}
    for x in rows:
        if x["status"] == "MATCH":
            kind = x["level"].split(" (")[0]
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
    except (DataError, DecimalException, KeyError, ValueError, IndexError, TypeError, OSError, EOFError,
            zlib.error) as e:
        print(f"DATA ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        for x in rows:
            print(f"{x['status']:<8} {x['id']:<3} {x['claim']}")
            print(f"         stated: {x['stated']}  computed: {x['computed']}  [{x['level']}]")
            if x["note"]:
                print(f"         {x['note']}")
        counts, by_level = summary(rows)
        print(f"\nrows of the case: {counts['MATCH']} match, {counts['RECORDED']} recorded only; fail: {counts['FAIL']}")
        print("matches by evidence: " + ", ".join(f"{k}: {v}" for k, v in by_level.items()))
    return 1 if any(x["status"] == "FAIL" for x in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
