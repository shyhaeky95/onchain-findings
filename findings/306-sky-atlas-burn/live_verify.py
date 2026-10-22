#!/usr/bin/env python3
"""Re-read the on-chain and published facts of sky-ecosystem/next-gen-atlas#306 at fixed blocks.

Usage:
    ETH_ARCHIVE_RPC=<archive endpoint> [ETHERSCAN_API_KEY=<key>] python3 live_verify.py --check
    ETH_ARCHIVE_RPC=<archive endpoint> [ETHERSCAN_API_KEY=<key>] python3 live_verify.py --write

Calls and block headers go to ETH_ARCHIVE_RPC. Logs go to the Etherscan API v2 when
ETHERSCAN_API_KEY is set, otherwise to ETH_ARCHIVE_RPC as eth_getLogs (ranges are halved
whenever the endpoint refuses them). GITHUB_TOKEN, if set, is sent to api.github.com only,
to lift its 60-calls-an-hour limit. Only on-chain fields are kept. Endpoints and keys are never
written to disk.

--write also pins a recent block (head minus 64) for the post-report rows; --check re-reads at
the pinned block, so a later check compares like with like.

--check compares a fresh read with the files in data/:
    exit 0  identical
    exit 1  a historical read differs (blocks, calls or logs)
    exit 2  no usable endpoint, the endpoint does not serve history, or a read failed
    exit 3  only the public sources differ (GitHub, Atlas, forum, Sourcify)
    exit 4  a public source could not be read (the historical check still ran)
--write replaces the files, and refuses to write a partial snapshot.
"""
import argparse
import datetime
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import urllib.request
from pathlib import Path

CASE = Path(__file__).resolve().parent
sys.path.insert(0, str(CASE.parent.parent / "lib"))
import evm  # noqa: E402

SNAPSHOT = CASE / "data" / "live_snapshot.json"
BUYBACKS = CASE / "data" / "window_b_buybacks.json"

CHAINLOG = "0xda0ab1e0017debcd72be8599041a2aa3ba7e740f"
NAMES = ("SKY", "USDS", "MCD_FLAP", "MCD_PAUSE_PROXY", "MCD_SPLIT")
WINDOW_B_START = 22950000
CAST = 25775271        # executive spell cast: the 10% allocation takes effect
WINDOW_A_END = 25780667
SCALE_END = 25578353   # end of the report's shorter buyback-scale range
END_OF_AUGUST = int(datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc).timestamp())
END_OF_SEPTEMBER = int(datetime.datetime(2026, 10, 1, tzinfo=datetime.timezone.utc).timestamp())
ZERO = "0x" + "0" * 40
CHECKED_HOLDERS = ("0x0000000000000000000000000000000000000000", "0x000000000000000000000000000000000000dead",
                   "0x0000000000000000000000000000000000000001")
T_TRANSFER = evm.topic("Transfer(address,address,uint256)")

ATLAS_REPO = "sky-ecosystem/next-gen-atlas"
ATLAS_AT_REPORT = "2026-08-18T12:00:21Z"   # the report was posted at this time
ATLAS_MID_JULY = "2026-07-16T00:00:00Z"    # the report: the A.4.1.2.2.3 wording was checked back to a 2026-07-15 snapshot
CLAUSES = {"A.2.3.1.2.4": "5ce73730-4d5d-479c-b01e-40e87f072121", "A.4.1.2.2.3": "5b94d42a-28fa-40fa-8948-ba3113a4d5b8"}
QUOTES = {
    "A.2.3.1.2.4": ["Ten percent (10%) of Step 3 Capital is used by the Smart Burn Engine to buy back SKY, and the SKY "
                    "tokens acquired through these buybacks are burned."],
    "A.4.1.2.2.3": ["At the same time that no new SKY tokens are issued",
                    "SKY tokens are bought back and burned on a regular basis",
                    "As a result, the total supply of SKY tokens will continue to decrease over time."],
}
ABSENT = ("non-issuance", "not issued", "no explicit burn", "reduce the supply")
PRESENT = ("Smart Burn Engine", "USDS")
FORUM_TOPIC = "https://forum.skyeco.com/t/28153.json"
FORUM_QUOTES = ("There is no explicit burn function invoked in this process.",
                "The ten percent (10%) allocation is therefore implemented through non-issuance")
SOURCIFY = "https://sourcify.dev/server/v2/contract/1/0x56072C95FAA701256059aa122697B133aDEd9279"


class Reads:
    def __init__(self, rpc, logs):
        self.rpc, self.reader, self.blocks, self.calls, self.logs = rpc, logs, {}, [], []

    def block(self, n):
        if str(n) not in self.blocks:
            self.blocks[str(n)] = self.rpc.block(n)
        return self.blocks[str(n)]

    def word(self, label, to, data, block):
        self.block(block)
        out = self.rpc.call(to, data, block)
        if out.get("reverted") or len(out["result"]) < 66:
            raise evm.ReaderError(f"{label}: no value")
        self.calls.append(dict({"label": label, "block": block, "to": to.lower(), "data": data}, **out))
        return out["result"]

    def query(self, label, address, topics, lo, hi, store=None):
        self.block(hi)
        rows = evm.sort_logs(self.reader.logs(address, topics, lo, hi))
        (self.logs if store is None else store).append(evm.pack(label, address, topics, lo, hi, rows))
        return rows


def block_before(rpc, ts, lo, hi):
    """The last block with timestamp < ts, by bisection on block headers."""
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if rpc.block(mid)["timestamp"] < ts:
            lo = mid
        else:
            hi = mid
    return lo


def read_chain(r, now):
    if r.rpc.request("eth_chainId", []) != "0x1":
        raise evm.ReaderError("not Ethereum mainnet")
    a = {}
    for name in NAMES:
        key = name.encode().ljust(32, b"\0").hex()
        a[name] = evm.address_of(r.word(f"chainlog {name}", CHAINLOG, evm.calldata("getAddress(bytes32)", key), WINDOW_A_END))
    sky, usds, flap, proxy = a["SKY"], a["USDS"], a["MCD_FLAP"], a["MCD_PAUSE_PROXY"]
    flap_key = evm.calldata("getAddress(bytes32)", b"MCD_FLAP".ljust(32, b"\0").hex())
    for b in (WINDOW_B_START, SCALE_END):  # the long ranges reuse today's flapper and pair; check they held then
        r.word(f"chainlog MCD_FLAP@{b}", CHAINLOG, flap_key, b)
    pair = {b: evm.address_of(r.word(f"MCD_FLAP.pair()@{b}", flap, evm.selector("pair()"), b))
            for b in (WINDOW_B_START, SCALE_END, CAST, WINDOW_A_END)}
    if pair[CAST] != pair[WINDOW_A_END]:
        raise evm.ReaderError("the flapper's pair changed inside window A")
    pair = pair[CAST]
    for b in (WINDOW_B_START, CAST, WINDOW_A_END, now):
        r.word(f"SKY.totalSupply()@{b}", sky, evm.selector("totalSupply()"), b)
    for b in (CAST, WINDOW_A_END):
        for h in CHECKED_HOLDERS:
            r.word(f"SKY.balanceOf({h})@{b}", sky, evm.calldata("balanceOf(address)", evm.word_address(h)[2:]), b)
    r.word("MCD_SPLIT.burn()", a["MCD_SPLIT"], evm.selector("burn()"), WINDOW_A_END)

    zero, w = evm.word_address(ZERO), evm.word_address
    usds_kicks = [T_TRANSFER, w(flap), w(pair)]
    sky_bought = [T_TRANSFER, w(pair), w(proxy)]
    burns = r.query("SKY Transfer to 0x0, window B", sky, [T_TRANSFER, None, zero], WINDOW_B_START, WINDOW_A_END)
    r.query("SKY Transfer from 0x0, window B", sky, [T_TRANSFER, zero, None], WINDOW_B_START, WINDOW_A_END)
    for l in burns:
        r.block(l["blockNumber"])
    r.query("USDS MCD_FLAP -> pair, window A", usds, usds_kicks, CAST, WINDOW_A_END)
    r.query("SKY pair -> MCD_PAUSE_PROXY, window A", sky, sky_bought, CAST, WINDOW_A_END)
    big = []
    r.query("USDS MCD_FLAP -> pair, scale range", usds, usds_kicks, WINDOW_B_START, SCALE_END, store=big)
    r.query("SKY pair -> MCD_PAUSE_PROXY, scale range", sky, sky_bought, WINDOW_B_START, SCALE_END, store=big)

    # After the report: every burn since the cast, and the buybacks of 2026-08-17..31.
    later = r.query("SKY Transfer to 0x0, cast to pinned head", sky, [T_TRANSFER, None, zero], CAST, now)
    r.query("SKY Transfer from 0x0, cast to pinned head", sky, [T_TRANSFER, zero, None], CAST, now)
    for l in later:
        r.block(l["blockNumber"])
    aug = block_before(r.rpc, END_OF_AUGUST, WINDOW_A_END, now)
    sep = block_before(r.rpc, END_OF_SEPTEMBER, aug, now)
    for b in (aug, aug + 1, sep, sep + 1):
        r.block(b)
    r.query("USDS MCD_FLAP -> pair, cast to end of August", usds, usds_kicks, CAST, aug)
    r.query("SKY pair -> MCD_PAUSE_PROXY, cast to end of August", sky, sky_bought, CAST, aug)
    r.query("USDS MCD_FLAP -> pair, September", usds, usds_kicks, aug + 1, sep)
    r.query("SKY pair -> MCD_PAUSE_PROXY, September", sky, sky_bought, aug + 1, sep)
    return {"pinned_head": now, "end_of_august_block": aug, "end_of_september_block": sep, "addresses": dict(a, pair=pair),
            "blocks": r.blocks, "calls": r.calls, "logs": r.logs}, {"logs": big}


def archive_checks(rpc):
    """Fails unless the endpoint serves state at old blocks rather than silently answering `latest`."""
    key = b"MCD_SPLIT".ljust(32, b"\0").hex()
    data = evm.calldata("getAddress(bytes32)", key)
    old = rpc.call(CHAINLOG, data, 18000000)
    now = rpc.call(CHAINLOG, data, WINDOW_A_END)
    genesis = rpc.request("eth_getCode", [CHAINLOG, hex(1)])
    return {"code_at_block_1_empty": genesis in ("0x", "0x0"),
            "splitter_unset_at_18000000": old.get("reverted") is True or int(old.get("result", "0x1"), 16) == 0,
            "pass": genesis in ("0x", "0x0") and (old.get("reverted") is True or old.get("result") != now.get("result"))}


def http(url, accept="application/json"):
    headers = {"User-Agent": "Mozilla/5.0", "Accept": accept}
    if url.startswith("https://api.github.com/") and os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]  # optional: 60 unauthenticated calls an hour
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120).read()


def atlas_text(sha):
    """{path: text} of every content Markdown file in the Atlas repository at a commit."""
    out = {}
    with tarfile.open(fileobj=io.BytesIO(http(f"https://codeload.github.com/{ATLAS_REPO}/tar.gz/{sha}", "*/*")), mode="r:gz") as t:
        for m in t:
            if m.isfile() and "/content/" in m.name and m.name.endswith(".md"):
                out[m.name.split("/", 1)[1]] = t.extractfile(m).read().decode("utf-8")
    return out


def clause(files, uuid):
    """(document number, name, type, body) of the clause with this UUID, in either repository layout: one file
    per clause (front matter `id:`, `docNo:`, `name:`, `type:`), or one heading per clause
    (`# <docNo> - <name> [<type>]  <!-- UUID: … -->`)."""
    for text in files.values():
        fm = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
        if fm and re.search(rf"^id:\s*{re.escape(uuid)}\s*$", fm.group(1), re.M):
            meta = dict(re.findall(r"^(docNo|name|type):\s*(.*?)\s*$", fm.group(1), re.M))
            strip = lambda v: v.strip("'\"") if v else None
            return strip(meta.get("docNo")), strip(meta.get("name")), strip(meta.get("type")), text[fm.end():]
        head = re.search(rf"^#+ (\S+) - (.*?) \[([^\]]+)\]\s*<!-- UUID: {re.escape(uuid)} -->\s*$", text, re.M)
        if head:
            nxt = re.search(r"^#+ .*<!-- UUID: [0-9a-f-]{36} -->\s*$", text[head.end():], re.M)
            body = text[head.end():head.end() + nxt.start()] if nxt else text[head.end():]
            return head.group(1), head.group(2), head.group(3), body
    return None, None, None, None


def atlas_facts(sha):
    files = atlas_text(sha)
    flat = lambda t: re.sub(r"\s+", " ", t or "")
    every = "\n".join(files.values())
    return {"commit": sha, "content_files": len(files),
            "clauses": {no: dict(zip(("document_number", "name", "type"), clause(files, u)[:3]),
                                 quotes_present=[q in flat(clause(files, u)[3]) for q in QUOTES[no]],
                                 refers_to=sorted(n for n in CLAUSES if n != no and n in flat(clause(files, u)[3])))
                        for no, u in CLAUSES.items()},
            "absent_phrase_counts": {p: every.count(p) for p in ABSENT},
            "present_phrase_counts": {p: every.count(p) for p in PRESENT}}


def public_state():
    gh = lambda path: json.loads(http(f"https://api.github.com/repos/{path}", "application/vnd.github+json"))
    issue = gh(f"{ATLAS_REPO}/issues/306")
    comments = gh(f"{ATLAS_REPO}/issues/306/comments")
    first = comments[0] if comments else {}
    at_report = gh(f"{ATLAS_REPO}/commits?sha=main&until={ATLAS_AT_REPORT}&per_page=1")[0]
    mid_july = gh(f"{ATLAS_REPO}/commits?sha=main&until={ATLAS_MID_JULY}&per_page=1")[0]
    head = gh(f"{ATLAS_REPO}/commits/main")
    forum = json.loads(http(FORUM_TOPIC))
    post = next((p for p in forum["post_stream"]["posts"] if p["post_number"] == 1), {})
    cooked = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", post.get("cooked", "")))
    verified = json.loads(http(SOURCIFY))
    src = json.loads(http(SOURCIFY + "?fields=sources"))["sources"]
    sky_sol = next((v["content"] for k, v in src.items() if k.endswith("/Sky.sol")), "")
    lines = [l.strip() for l in sky_sol.splitlines()]
    writes = [i for i, l in enumerate(lines) if re.match(r"totalSupply\s*=", l)]
    return {
        "issue_306": {"state": issue["state"], "closed_at": issue["closed_at"]},
        "maintainer_reply": {"author": first.get("user", {}).get("login"), "association": first.get("author_association"),
                             "created_at": first.get("created_at"),
                             "says_burned_in_future_executive_vote": "burned in a future executive vote" in (first.get("body") or ""),
                             "says_no_atlas_change": "no change to the Atlas is required" in (first.get("body") or "")},
        "atlas_at_report": dict(atlas_facts(at_report["sha"]), committed_at=at_report["commit"]["committer"]["date"]),
        "atlas_now": atlas_facts(head["sha"]),
        "atlas_mid_july": dict(atlas_facts(mid_july["sha"]), committed_at=mid_july["commit"]["committer"]["date"]),
        "forum_28153_post_1": {"author": post.get("username"), "created_at": post.get("created_at"),
                               "quotes_present": [q in cooked for q in FORUM_QUOTES]},
        "sky_sol": {"sha256": hashlib.sha256(sky_sol.encode()).hexdigest(), "source_files": sorted(src),
                    "sourcify_match": {k: verified.get(k) for k in ("match", "runtimeMatch", "creationMatch")},
                    "totalSupply_writes": [lines[i] for i in writes],
                    "next_emit_after_each_write": [next((l for l in lines[i + 1:] if l.startswith("emit ")), None) for i in writes]},
    }


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


def write_rows(path, data):
    """One stored log row per line, so the large file stays diffable."""
    out = ['{"logs": [']
    for qi, q in enumerate(data["logs"]):
        head = {k: v for k, v in q.items() if k != "rows"}
        out.append(" " + json.dumps(head)[:-1] + ', "rows": [')
        out += ["  " + json.dumps(r) + ("," if i < len(q["rows"]) - 1 else "") for i, r in enumerate(q["rows"])]
        out.append(" ]}" + ("," if qi < len(data["logs"]) - 1 else ""))
    out.append("]}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


HISTORICAL = ("pinned_head", "end_of_august_block", "end_of_september_block", "addresses", "blocks", "calls", "logs")


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
    saved = None if args.write else json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    try:
        checks = archive_checks(rpc)
        if not checks["pass"]:
            print(f"endpoint does not serve history: {checks}", file=sys.stderr)
            return 2
        now = int(rpc.request("eth_blockNumber", []), 16) - 64 if args.write else saved["pinned_head"]
        fresh, big = read_chain(Reads(rpc, logs), now)
    except (evm.ReaderError,) + evm.TRANSPORT_ERRORS as e:
        print(f"a read failed, nothing compared: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    try:
        public, public_error = public_state(), None
    except Exception as e:  # network or API failure; the historical part still counts
        public, public_error = None, type(e).__name__
    reader = "Etherscan API v2 getLogs" if isinstance(logs, evm.Etherscan) else "archive JSON-RPC eth_getLogs"
    if args.write:
        if public is None:
            print(f"public sources unreadable ({public_error}); nothing written", file=sys.stderr)
            return 4
        snap = {"case": "sky-ecosystem/next-gen-atlas#306",
                "checked_at_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "readers": {"calls": "archive JSON-RPC (endpoint not recorded)", "logs": reader},
                "archive_checks": checks, **fresh, "public": public}
        SNAPSHOT.write_text(json.dumps(snap, indent=1) + "\n", encoding="utf-8")
        write_rows(BUYBACKS, big)
        print(f"wrote {SNAPSHOT.name} and {BUYBACKS.name}: {len(fresh['calls'])} calls, "
              f"{sum(len(q['rows']) for q in fresh['logs'] + big['logs'])} logs, pinned head {now}")
        return 0
    hist = diff({k: saved[k] for k in HISTORICAL}, fresh) + diff(json.loads(BUYBACKS.read_text(encoding="utf-8")), big, "/window_b")
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
