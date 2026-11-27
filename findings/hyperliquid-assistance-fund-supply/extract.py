#!/usr/bin/env python3
"""Write the data files of this case from the saved responses, or check that they were written this way.

Usage:
    python3 extract.py --from RAW_DIR            # write data/ from the saved responses
    python3 extract.py --from RAW_DIR --check    # rebuild in memory and compare with data/; exit 1 if different

RAW_DIR holds the responses as they were saved on 2026-11-26 and 2026-11-27:
    info_tokenDetails.json, info_spot_AF.json            Hyperliquid Info API (published here, the first gzipped)
    tweet_<id>.json, fx_status_<id>.json                 the two X posts, through two readers (not published)
    coingecko_hyperliquid.json                           CoinGecko /coins/hyperliquid (not published)
    docs_trading_fees.html, docs_hip1.html               Hyperliquid docs: Trading > Fees, HIP-1 (not published)
Only the two Info responses are copied into data/. For the other sources the case keeps the quoted
sentences, which reader returned each, a few fields, and the SHA-256 of each response. Readers who do
not hold RAW_DIR can read this script to see how data/public_sources.json was made.
"""
import argparse
import gzip
import hashlib
import html
import json
import re
import sys
from pathlib import Path

CASE = Path(__file__).resolve().parent
AF = "0x" + "fe" * 20
HYPE_TOKEN_ID = "0x0d01dc56dcaaca66ad901c959b4011ec"
INFO_ENDPOINT = "https://api.hyperliquid.xyz/info"
DOCS_URL = "https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees"
HIP1_URL = "https://hyperliquid.gitbook.io/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-1-native-token-standard"
POSTS = (("2001127850754367525", "proposal"), ("2003684257949188396", "result"))
# Capture times are file times of the saved responses; they cannot be derived from the bytes.
CAPTURED = {
    "info": "2026-11-26T09:56:10Z",
    "x_posts": "2026-11-26T09:55:27Z to 09:55:53Z",
    "coingecko": "2026-11-26T09:56:11Z",
    "docs": "2026-11-27T05:51:13Z",
    "hip1": "2026-11-27T06:45:04Z",
}
QUOTES = {
    "2001127850754367525": [
        "The Hyper Foundation is proposing a validator vote to formally recognize the Assistance Fund HYPE as burned, "
        "removing the tokens permanently from the circulating and total supply.",
        "Similar to the zero address, the Assistance Fund system address has never had a private key with control "
        "over its funds.",
        "By voting “Yes,” validators agree to treat the Assistance Fund HYPE as burned.",
        "No onchain action is required, as the tokens are already in a system address with no private key.",
        "This vote is binding social consensus to never authorize a protocol upgrade to access this address.",
    ],
    "2003684257949188396": [
        "HYPE in the Assistance Fund system address of 0xfefefefefefefefefefefefefefefefefefefefe has been formally "
        "recognized as burned.",
        "The governance vote was based on stake-weighted consensus, with 85% of stake voting for burning, 7% against, "
        "and 8% abstaining.",
    ],
}
DOCS_QUOTE = "HYPE in the assistance fund is burned, removing the tokens permanently from the circulating and total supply."
HIP1_QUOTES = [
    "maxSupply : the maximum and initial supply.",
    "The supply may decrease over time due to spot order book fees or future burn mechanisms.",
]
PAGES = (("fees", DOCS_URL, "docs_trading_fees.html", "docs", [DOCS_QUOTE]),
         ("hip1", HIP1_URL, "docs_hip1.html", "hip1", HIP1_QUOTES))
TOKEN_DETAILS = "data/info_tokenDetails_HYPE.json.gz"
SPOT_AF = "data/info_spotClearinghouseState_AF.json"
PUBLIC = "data/public_sources.json"


def sha(b):
    return hashlib.sha256(b).hexdigest()


def page_text(raw):
    """Visible text of an HTML page: scripts, styles and tags removed, entities decoded, spaces collapsed."""
    page = re.sub(r"(?is)<(script|style|noscript|template)\b[^>]*>.*?</\1\s*>", " ", raw.decode("utf-8", errors="replace"))
    text = html.unescape(re.sub(r"<[^>]+>", " ", page))
    return re.sub(r"\s+", " ", text)


def page_title(raw):
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw.decode("utf-8", errors="replace"))
    return re.sub(r"\s+", " ", html.unescape(m.group(1))).strip() if m else ""


def _page(r, label, url, name, captured_key, quotes):
    raw = (r / name).read_bytes()
    text = page_text(raw)
    return {"label": label, "url": url, "title": page_title(raw), "captured_utc": CAPTURED[captured_key],
            "response_sha256": sha(raw), "quotes": [{"text": q, "found": q in text} for q in quotes]}


def build(raw_dir):
    r = Path(raw_dir)
    td, spot = (r / "info_tokenDetails.json").read_bytes(), (r / "info_spot_AF.json").read_bytes()
    if (r / "probe_utc.txt").read_text().strip() != CAPTURED["info"]:
        raise ValueError("probe time in RAW_DIR differs from the recorded capture time")
    posts = []
    for pid, label in POSTS:
        sy_raw, fx_raw = (r / f"tweet_{pid}.json").read_bytes(), (r / f"fx_status_{pid}.json").read_bytes()
        sy, fx = json.loads(sy_raw), json.loads(fx_raw)["tweet"]
        if not sy["id_str"] == pid == fx["id"]:
            raise ValueError(f"post id differs in RAW_DIR for {pid}")
        texts = {"syndication": sy["text"], "fxtwitter": fx["text"]}
        posts.append({
            "label": label, "id": pid, "url": f"https://x.com/HyperFND/status/{pid}",
            "readers": {
                "syndication": {"endpoint": "cdn.syndication.twimg.com/tweet-result", "account": sy["user"]["screen_name"],
                                "created_at": sy["created_at"], "text_chars": len(sy["text"]), "response_sha256": sha(sy_raw)},
                "fxtwitter": {"endpoint": "api.fxtwitter.com", "account": fx["author"]["screen_name"],
                              "created_at": fx["created_at"], "text_chars": len(fx["text"]), "response_sha256": sha(fx_raw)},
            },
            "quotes": [{"text": q, "found_by": {k: q in t for k, t in texts.items()}} for q in QUOTES[pid]],
        })
    cg_raw = (r / "coingecko_hyperliquid.json").read_bytes()
    cg = json.loads(cg_raw, parse_float=str)
    fields = {"market_data.total_supply": cg["market_data"]["total_supply"], "last_updated": cg["last_updated"]}
    for v in fields.values():
        if v.encode() not in cg_raw:
            raise ValueError(f"CoinGecko field not found as text in the response: {v}")
    public = {
        "about": "Facts about public sources as captured. The X posts, the CoinGecko response and the documentation page "
                 "are not copied here: each is linked, its quoted sentences or fields are stored with where they were "
                 "found, and each response is listed by SHA-256. Written by extract.py.",
        "captured_utc": CAPTURED,
        "x_posts": posts,
        "docs": [_page(r, *page) for page in PAGES],
        "coingecko": {"page": "https://www.coingecko.com/en/coins/hyperliquid",
                      "request": "GET https://api.coingecko.com/api/v3/coins/hyperliquid",
                      "fields": fields, "response_sha256": sha(cg_raw), "response_published": False},
        "info": {"endpoint": INFO_ENDPOINT, "captured_utc": CAPTURED["info"],
                 "requests": {TOKEN_DETAILS: {"type": "tokenDetails", "tokenId": HYPE_TOKEN_ID},
                              SPOT_AF: {"type": "spotClearinghouseState", "user": AF}},
                 "response_sha256": {TOKEN_DETAILS: sha(td), SPOT_AF: sha(spot)}},
    }
    return {
        TOKEN_DETAILS: gzip.compress(td, compresslevel=9, mtime=0),
        SPOT_AF: spot,
        PUBLIC: (json.dumps(public, indent=1, ensure_ascii=False) + "\n").encode("utf-8"),
    }


def main(argv=None, case=CASE):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from", dest="raw", required=True, metavar="RAW_DIR")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    try:
        files = build(args.raw)
    except (OSError, ValueError, KeyError) as e:
        print(f"cannot build from {args.raw}: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    if args.check:
        differ = [p for p, b in files.items() if (case / p).read_bytes() != b]
        for p in files:
            print(f"{p}: {'DIFFERS' if p in differ else 'identical'}")
        return 1 if differ else 0
    for p, b in files.items():
        (case / p).write_bytes(b)
    print("written: " + ", ".join(files) + " (run reproduce.py --update-hashes next)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
