"""Tests for findings/20659-defillama-uniswap-v4-bsc.

Every test works on a temporary copy of the case, so the committed data is never touched.

MUTATIONS holds input changes that must each turn one named row into FAIL. There is one per
condition a row checks: rows that test several things (R6, L5, A8, O2, ...) get several,
and every erratum gets both directions, "the documented correction no longer holds" and
"the report's statement has become true". CONDITIONS records how many each row needs.
"""
import contextlib
import importlib.util
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

CASE = Path(__file__).resolve().parent.parent / "findings" / "20659-defillama-uniswap-v4-bsc"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reproduce = load_module("reproduce_20659", CASE / "reproduce.py")
live_verify = load_module("live_verify_20659", CASE / "live_verify.py")

EXT = reproduce.EXTRACT
REC = "data/recorded.json"
SNAP = "data/live_snapshot.json"
BLK, RUN = reproduce.ISSUE_BLOCK, reproduce.RUN_BLOCK
LISTED = reproduce.REPORT["remaining_holdings"]


def at(d, path):
    for k in path:
        d = d[k]
    return d


def setter(path, value):
    def mutate(d):
        parent = at(d, path[:-1])
        parent[path[-1]] = value(parent[path[-1]]) if callable(value) else value
    return mutate


def points(d, key):
    return d["responses"][key]["points"]


def tvl_point(d, chain, date):
    return next(p for p in points(d, "protocol")["chainTvls"][chain]["tvl"] if reproduce.day(p["date"]) == date)


def tvl(chain, date, f):
    def mutate(d):
        p = tvl_point(d, chain, date)
        p["totalLiquidityUSD"] = f(p["totalLiquidityUSD"], d)
    return mutate


def bump_volume(d):
    for ts, by_chain in points(d, "dexs")["totalDataChartBreakdown"]:
        if reproduce.day(ts) == "2026-08-23":
            v = by_chain["BSC"]
            if isinstance(v, dict):
                v[next(iter(v))] += 1
            else:
                by_chain["BSC"] = v + 1


def bump_chain_tvl(d):
    next(p for p in points(d, "chain_tvl") if reproduce.day(p["date"]) == "2026-08-23")["tvl"] += 20_000_000


def enum_total_for_remainder(remainder):
    def mutate(d):
        d["enumeration"]["total_priced_usd"] = d["enumeration"]["top_token"]["usd"] + remainder
    return mutate


ORIGINAL_TVL24 = reproduce.REPORT["tvl"]["2026-08-24"]

MUTATIONS = [
    ("R1", "08-22 TVL", EXT, tvl("Binance", "2026-08-22", lambda v, d: v + 1)),
    ("R2", "08-23 TVL", EXT, tvl("Binance", "2026-08-23", lambda v, d: v + 1)),
    ("R3", "08-24 TVL", EXT, tvl("Binance", "2026-08-24", lambda v, d: v + 1)),
    ("R4", "08-23 volume", EXT, bump_volume),
    ("R5", "08-23 chain TVL", EXT, bump_chain_tvl),
    ("R6", "correction: 08-22->08-24 span", EXT, tvl("Binance", "2026-08-24", lambda v, d: v * 1.2)),
    ("R6", "correction: 08-22->08-23 step", EXT, tvl("Binance", "2026-08-23", lambda v, d: v * 1.1)),
    ("R6", "statement: span becomes 121x", EXT,
     tvl("Binance", "2026-08-24", lambda v, d: tvl_point(d, "Binance", "2026-08-22")["totalLiquidityUSD"] * 121)),
    ("R7", "Ethereum TVL", EXT, tvl("Ethereum", "2026-08-24", lambda v, d: v + 1)),
    ("L1", "name", SNAP, setter(["blocks", BLK, "token", "name"], "SAND2")),
    ("L1", "symbol", SNAP, setter(["blocks", BLK, "token", "symbol"], "SANDX")),
    ("L2", "totalSupply", SNAP, setter(["blocks", BLK, "token", "total_supply"], lambda v: v + 1)),
    ("L3", "balance", SNAP, setter(["blocks", BLK, "token", "pool_manager_balance"], lambda v: v + 1)),
    ("L4", "pegged name", SNAP, setter(["blocks", BLK, "pegged_sand", "name"], "Sandbox")),
    ("L4", "pegged totalSupply", SNAP, setter(["blocks", BLK, "pegged_sand", "total_supply"], lambda v: v + 1)),
    ("L5", "correction: balance at the named block", SNAP,
     setter(["blocks", BLK, "pool_manager_major_balances", "USDT"], lambda v: v + 1000)),
    ("L5", "correction: recorded copy of the report's list (round-2 attack)", REC,
     setter(["remaining_holdings_listed_in_issue", "amounts", "USDT"], 9_999_999_999)),
    ("L5", "correction: list made to match the run block", SNAP,
     setter(["blocks", RUN, "pool_manager_major_balances"], dict(LISTED))),
    ("L5", "correction: USDT gap to the run block (round-3 attack)", SNAP,
     setter(["blocks", RUN, "pool_manager_major_balances", "USDT"], 20_000_000)),
    ("L5", "statement: balances at the named block equal the list", SNAP,
     setter(["blocks", BLK, "pool_manager_major_balances"], dict(LISTED))),
    ("L6", "sha256", SNAP, setter(["public", "adapter", "before_fix", "sha256"], "0" * 64)),
    ("L6", "blacklist size", SNAP, setter(["public", "adapter", "before_fix", "bsc_blacklist_entries"], 6)),
    ("L6", "token already listed", SNAP, setter(["public", "adapter", "before_fix", "token_blacklisted"], True)),
    ("L7", "BLOTIX/SAFEMONEY", SNAP,
     setter(["public", "adapter", "before_fix", "ethereum_blacklists_blotix_and_safemoney"], False)),
    ("A1", "correction: balance x price", REC, setter(["prices", "coins_llama_at_issue", "price_usd"], 0.06)),
    ("A1", "correction: enumeration figure", REC, setter(["enumeration", "top_token", "usd"], 5_700_000_000)),
    ("A1", "correction: implied price equals a recorded price (round-3 attack)", REC,
     setter(["prices", "coins_llama_at_run", "price_usd"], 0.044915)),
    ("A1", "statement: balance x price becomes $5.63B", REC,
     setter(["prices", "coins_llama_at_issue", "price_usd"], 0.0449)),
    ("A2", "GeckoTerminal price", REC, setter(["prices", "geckoterminal_at_run", "price_usd"], lambda v: v * 3)),
    ("A3", "price ratio", REC, setter(["prices", "coins_llama_at_issue", "price_usd"], lambda v: v * 2)),
    ("A4", "enumerated total", REC, setter(["enumeration", "total_priced_usd"], lambda v: v - 1_000_000_000)),
    ("A5", "token value", REC, setter(["enumeration", "top_token", "usd"], lambda v: v - 100_000_000)),
    ("A5", "token share", REC, setter(["enumeration", "total_priced_usd"], lambda v: v + 1_000_000_000)),
    ("A6", "correction: remainder", REC, setter(["enumeration", "top_token", "usd"], lambda v: v + 20_000_000)),
    ("A6", "correction: basket size", REC, setter(["major_token_baskets", "bsc", "total_usd"], 100_000_000)),
    ("A6", "statement: remainder becomes $17M", REC, enum_total_for_remainder(17_000_000)),
    ("A7", "basket figure", REC, setter(["major_token_baskets", "ethereum", "total_usd"], lambda v: v + 5)),
    ("A7", "order of magnitude", EXT, tvl("Ethereum", "2026-08-24", lambda v, d: v * 100)),
    ("A8", "correction: whole remainder ratio", REC, setter(["enumeration", "total_priced_usd"], lambda v: v + 100_000_000)),
    ("A8", "correction: basket ratio (round-2 attack)", REC, setter(["major_token_baskets", "bsc", "total_usd"], 100_000_000)),
    ("A8", "correction: Ethereum control ratio", REC,
     setter(["major_token_baskets", "ethereum", "total_usd"], lambda v: v * 0.5)),
    ("A8", "statement: remainder ratio becomes 0.0029x", REC, enum_total_for_remainder(ORIGINAL_TVL24 * 0.0029)),
    ("A9", "FDV / price", REC, setter(["prices", "geckoterminal_at_run", "fdv_usd"], lambda v: v * 1.01)),
    ("C1", "price", REC, setter(["prices", "coins_llama_at_issue", "price_usd"], lambda v: v * 1.0000001)),
    ("C1", "confidence", REC, setter(["prices", "coins_llama_at_issue", "confidence"], 0.5)),
    ("C2", "price", REC, setter(["prices", "geckoterminal_at_run", "price_usd"], lambda v: v * 1.001)),
    ("C2", "FDV", REC, setter(["prices", "geckoterminal_at_run", "fdv_usd"], lambda v: v + 1)),
    ("C2", "24h volume", REC, setter(["prices", "geckoterminal_at_run", "volume_24h_usd"], 5.0)),
    ("C3", "token count", REC, setter(["enumeration", "tokens_nonzero"], 1)),
    ("C4", "pegged price", REC, setter(["prices", "coins_llama_pegged_sand_at_run", "price_usd"], 0.05)),
    ("C5", "coingecko price", REC, setter(["prices", "coingecko_the_sandbox_at_issue", "price_usd"], 0.05)),
    ("O1", "price returns", SNAP, setter(["public", "coins_llama_current", "token"], {"coins": {"bsc:x": {"price": 1}}})),
    ("O2", "not merged", SNAP, setter(["public", "pr_20690", "merged_at"], None)),
    ("O2", "merge commit", SNAP, setter(["public", "pr_20690", "merge_commit_sha"], "abc")),
    ("O2", "token not listed", SNAP, setter(["public", "adapter", "fix_20690", "token_blacklisted"], False)),
    ("O2", "blacklist size", SNAP, setter(["public", "adapter", "fix_20690", "bsc_blacklist_entries"], 5)),
    ("O3", "history not revised", SNAP, setter(["public", "defillama_bsc_tvl_now", "2026-08-23"], 4854351010)),
    ("O3", "outside $30M-$60M", SNAP, setter(["public", "defillama_bsc_tvl_now", "2026-08-22"], 100_000_000)),
    ("O4", "state", SNAP, setter(["public", "issue_20659", "state"], "open")),
    ("O4", "state reason", SNAP, setter(["public", "issue_20659", "state_reason"], "not_planned")),
    ("O4", "closing date", SNAP, setter(["public", "issue_20659", "closed_at"], "2026-09-01T00:00:00Z")),
]

CONDITIONS = {"R6": 3, "L1": 2, "L4": 2, "L5": 5, "L6": 3, "A1": 4, "A5": 2, "A6": 3, "A7": 2, "A8": 4,
              "C1": 2, "C2": 3, "O2": 4, "O3": 2, "O4": 3}
ERRATA = {"R6", "L5", "A1", "A6", "A8"}


def silent(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*args, **kwargs)


class CaseCopy(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.case = self.copy("case")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def copy(self, name):
        dest = self.tmp / name
        shutil.copytree(CASE, dest)
        return dest

    def edit(self, case, rel, mutate):
        """Apply a change and accept it into the manifest, as a consistent but wrong dataset would be."""
        p = case / rel
        data = json.loads(p.read_text())
        mutate(data)
        p.write_text(json.dumps(data))
        manifest = json.loads((case / "MANIFEST.json").read_text())
        for f in manifest["files"]:
            if f["path"] == rel:
                f["sha256"], f["bytes"] = reproduce.sha256(p), p.stat().st_size
        (case / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))

    @staticmethod
    def row(rows, cid):
        return next(r for r in rows if r["id"] == cid)


class TestPristine(CaseCopy):
    def test_exit_zero_and_no_fail(self):
        self.assertEqual(silent(reproduce.main, [], case=self.case), 0)
        self.assertFalse([r["id"] for r in reproduce.results(self.case) if r["status"] == "FAIL"])

    def test_statuses(self):
        rows = reproduce.results(self.case)
        by = lambda s: {r["id"] for r in rows if r["status"] == s}
        self.assertEqual(by("ERRATUM"), ERRATA)
        self.assertEqual(by("RECORDED"), {"R1", "R2", "R3", "R4", "R5", "R7", "C1", "C2", "C3", "C4", "C5"})
        self.assertEqual(by("OBSERVED"), {"O1", "O2", "O3", "O4"})

    def test_runs_without_network(self):
        import socket

        def refuse(*a, **k):
            raise AssertionError("reproduce.py must not open a network connection")

        with mock.patch.object(socket, "socket", refuse), mock.patch.object(socket, "create_connection", refuse):
            self.assertEqual(silent(reproduce.main, [], case=self.case), 0)

    def test_day_is_utc(self):
        self.assertEqual(reproduce.day(1787529600), "2026-08-24")
        self.assertEqual(reproduce.day(1787529599), "2026-08-23")


class TestEveryCondition(CaseCopy):
    def test_coverage(self):
        ids = {r["id"] for r in reproduce.results(self.case)}
        counts = {cid: sum(m[0] == cid for m in MUTATIONS) for cid in ids}
        self.assertEqual(set(m[0] for m in MUTATIONS), ids)
        for cid in ids:
            with self.subTest(claim=cid):
                self.assertGreaterEqual(counts[cid], CONDITIONS.get(cid, 1))
        for cid in ERRATA:
            labels = [m[1] for m in MUTATIONS if m[0] == cid]
            with self.subTest(erratum=cid):
                self.assertTrue(any(l.startswith("statement:") for l in labels))
                self.assertTrue(any(l.startswith("correction:") for l in labels))

    def test_each_mutation_fails_its_row(self):
        for i, (cid, label, rel, mutate) in enumerate(MUTATIONS):
            with self.subTest(claim=cid, mutation=label):
                case = self.copy(f"m{i}")
                self.edit(case, rel, mutate)
                row = self.row(reproduce.results(case), cid)
                self.assertEqual(row["status"], "FAIL")
                if label.startswith("statement:"):
                    self.assertIn("erratum is wrong", row["note"])
                elif label.startswith("correction:"):
                    self.assertIn("correction no longer holds", row["note"])
                self.assertEqual(silent(reproduce.main, [], case=case), 1)


class TestIntegrity(CaseCopy):
    def test_tampered_extract_is_rejected(self):
        p = self.case / EXT
        b = bytearray(p.read_bytes())
        b[100] = ord("7") if b[100] != ord("7") else ord("8")
        p.write_bytes(bytes(b))
        with self.assertRaises(reproduce.DataError):
            reproduce.results(self.case)
        self.assertEqual(silent(reproduce.main, [], case=self.case), 1)

    def test_missing_file_is_rejected(self):
        (self.case / REC).unlink()
        with self.assertRaisesRegex(reproduce.DataError, "missing"):
            reproduce.results(self.case)

    def test_unlisted_file_is_rejected(self):
        (self.case / "data/extra.json").write_text("{}")
        with self.assertRaisesRegex(reproduce.DataError, "not in MANIFEST"):
            reproduce.results(self.case)

    def test_update_hashes_never_rewrites_the_extract(self):
        p = self.case / EXT
        p.write_bytes(p.read_bytes() + b" ")
        with self.assertRaises(reproduce.DataError):
            reproduce.update_hashes(self.case)

    def test_two_points_on_one_day_are_rejected(self):
        def duplicate(d):
            p = tvl_point(d, "Binance", "2026-08-23")
            points(d, "protocol")["chainTvls"]["Binance"]["tvl"].append({**p, "date": p["date"] + 3600})
        self.edit(self.case, EXT, duplicate)
        with self.assertRaisesRegex(reproduce.DataError, "two data points"):
            reproduce.results(self.case)


class TestExtract(CaseCopy):
    """--check-extract, against stand-ins for the three full responses (which are not published)."""

    def full_responses(self, change=None):
        """The extract's points plus days, chains and fields that the extract must leave out."""
        pts = {k: v["points"] for k, v in json.loads((self.case / EXT).read_text())["responses"].items()}
        before = 1787270400  # 2026-08-21
        protocol = {"name": "Uniswap V4", "chainTvls": pts["protocol"]["chainTvls"]}
        protocol["chainTvls"]["Arbitrum"] = {"tvl": [{"date": before, "totalLiquidityUSD": 1}]}
        for chain in ("Binance", "Ethereum"):
            protocol["chainTvls"][chain]["tvl"].insert(0, {"date": before, "totalLiquidityUSD": 1})
        protocol["chainTvls"]["Ethereum"]["tvl"].insert(1, {"date": 1787356800, "totalLiquidityUSD": 2})
        rows = pts["dexs"]["totalDataChartBreakdown"]
        for _, by_chain in rows:
            by_chain["Ethereum"] = {"Uniswap V4": 3}
        dexs = {"total24h": 4, "totalDataChartBreakdown": [[before, {"BSC": {"Uniswap V4": 5}}]] + rows}
        chain = [{"date": before, "tvl": 6}] + pts["chain_tvl"]
        full = {"protocol": protocol, "dexs": dexs, "chain_tvl": chain}
        if change:
            change(full)
        src = self.tmp / "responses"
        src.mkdir()
        manifest = json.loads((self.case / "MANIFEST.json").read_text())
        entry = next(f for f in manifest["files"] if f["path"] == EXT)
        for s in entry["derived_from"]:
            p = src / s["file"]
            p.write_text(json.dumps(full[s["key"]]))
            s["sha256"], s["bytes"] = reproduce.sha256(p), p.stat().st_size
        (self.case / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))
        return src

    def test_rebuilds_byte_for_byte(self):
        self.assertEqual(silent(reproduce.main, ["--check-extract", str(self.full_responses())], case=self.case), 0)

    def test_different_figure_is_reported(self):
        def bump(full):
            full["chain_tvl"][-1]["tvl"] += 1
        self.assertEqual(silent(reproduce.main, ["--check-extract", str(self.full_responses(bump))], case=self.case), 1)

    def test_unlisted_response_is_rejected(self):
        src = self.full_responses()
        p = next(src.iterdir())
        p.write_text(p.read_text() + " ")
        with self.assertRaisesRegex(reproduce.DataError, "not the response listed"):
            reproduce.build_extract(src, self.case)
        self.assertEqual(silent(reproduce.main, ["--check-extract", str(src)], case=self.case), 1)


class TestLiveCheck(CaseCopy):
    """live_verify.py --check against a stand-in for the archive node and public sources."""

    def run_check(self, blocks=None, public=None, argv=("--check",)):
        snap = json.loads((self.case / SNAP).read_text())
        blocks = blocks or snap["blocks"]

        def public_state():
            if isinstance(public, Exception):
                raise public
            return public or snap["public"]

        with mock.patch.object(live_verify, "SNAPSHOT", self.case / SNAP), \
                mock.patch.object(live_verify, "archive_checks", lambda rpc, b: snap["archive_checks"]), \
                mock.patch.object(live_verify, "read_block", lambda rpc, b: blocks[str(b)]), \
                mock.patch.object(live_verify, "public_state", public_state):
            return silent(live_verify.main, list(argv), rpc=object())

    def changed_blocks(self):
        snap = json.loads((self.case / SNAP).read_text())
        snap["blocks"][BLK]["token"]["pool_manager_balance"] += 1
        return snap["blocks"]

    def test_identical_is_0(self):
        self.assertEqual(self.run_check(), 0)

    def test_historical_difference_is_1(self):
        self.assertEqual(self.run_check(blocks=self.changed_blocks()), 1)

    def test_public_change_is_3(self):
        public = json.loads((self.case / SNAP).read_text())["public"]
        public["coins_llama_current"]["token"] = {"coins": {"bsc:x": {"price": 1}}}
        self.assertEqual(self.run_check(public=public), 3)

    def test_public_failure_still_checks_history(self):
        self.assertEqual(self.run_check(public=RuntimeError("rate limited")), 4)
        self.assertEqual(self.run_check(blocks=self.changed_blocks(), public=RuntimeError("rate limited")), 1)

    def test_write_refuses_partial_snapshot(self):
        before = (self.case / SNAP).read_bytes()
        self.assertEqual(self.run_check(public=RuntimeError("rate limited"), argv=("--write",)), 4)
        self.assertEqual((self.case / SNAP).read_bytes(), before)



class TestLiveParsing(unittest.TestCase):
    """The parts of live_verify.py that turn raw RPC replies and adapter source into the snapshot."""

    @staticmethod
    def abi_string(text):
        b = text.encode()
        return "0x" + (32).to_bytes(32, "big").hex() + len(b).to_bytes(32, "big").hex() + b.ljust(32, b"\0").hex()

    def test_read_block_decodes_replies(self):
        lv = live_verify
        supply, balance = 11976071028505969 * 10 ** 15, 12541022675191876 * 10 ** 13
        major = {a.lower(): (i + 1) * 10 ** 18 for i, a in enumerate(lv.MAJOR.values())}

        class FakeRpc:
            def result(self, method, params):
                assert method == "eth_getBlockByNumber"
                return {"hash": "0x" + "ab" * 32, "timestamp": hex(1787543726)}

            def call(self, to, data, block):
                to = to.lower()
                if data == lv.SEL["name"]:
                    return TestLiveParsing.abi_string("SAND" if to == lv.TOKEN else "The Sandbox")
                if data == lv.SEL["symbol"]:
                    return TestLiveParsing.abi_string("SAND")
                if data == lv.SEL["decimals"]:
                    return hex(18)
                if data == lv.SEL["totalSupply"]:
                    return hex(supply if to == lv.TOKEN else 18132974098678216 * 10 ** 9)
                assert data == lv.SEL["balanceOf"] + lv.POOL_MANAGER[2:].rjust(64, "0")
                return hex(balance if to == lv.TOKEN else major[to])

        out = lv.read_block(FakeRpc(), 117739591)
        self.assertEqual(out["timestamp_utc"], "2026-08-24T03:55:26Z")
        self.assertEqual((out["token"]["name"], out["token"]["symbol"], out["token"]["decimals"]), ("SAND", "SAND", 18))
        self.assertAlmostEqual(out["token"]["total_supply"], 11976071028505.969, places=2)
        self.assertAlmostEqual(out["token"]["pool_manager_balance"], 125410226751.91876, places=2)
        self.assertEqual(out["pegged_sand"]["name"], "The Sandbox")
        self.assertAlmostEqual(out["pegged_sand"]["total_supply"], 18132974.098678216, places=2)
        self.assertEqual(out["pool_manager_major_balances"], {"USDT": 1.0, "USDC": 2.0, "WBNB": 3.0, "BTCB": 4.0})

    def test_blacklist_reads_only_its_own_chain(self):
        src = """module.exports = {
  ethereum: { factory: "0x000000000004444c5dc75cB358380D2e3dE08A90", fromBlock: 1, blacklistedTokens: ['0xb4357054c3da8d46ed642383f03139ac7f090343',
    '0xbdb37597c7e84d898a5536fbb6e4a5c2bcac640b', // BLOTIX
    '0xc4abeddacfa65e25d6c942861fb3bdc9c00eeb13', // SAFEMONEY
  ]},
  unichain: { factory: "0x1F98400000000000000000000000000000000004", fromBlock: 1 },
  bsc: { factory: "0x28e2ea090877bf75740558f6bfb36a5ffee9e9df", fromBlock: 3, blacklistedTokens: ['0xb4357054c3dA8D46eD642383F03139aC7f090343',
    '0xac531eb26ca1d21b85126de8fb87e80e09002dcf' // fake SAND
  ] },
}"""
        bsc = live_verify.blacklist(src, "bsc")
        self.assertEqual(len(bsc), 2)
        self.assertIn(live_verify.TOKEN, bsc)
        self.assertNotIn("0x28e2ea090877bf75740558f6bfb36a5ffee9e9df", bsc)
        eth = live_verify.blacklist(src, "ethereum")
        self.assertTrue(live_verify.BLOTIX in eth and live_verify.SAFEMONEY in eth)
        self.assertEqual(live_verify.blacklist(src, "unichain"), [])
        self.assertEqual(live_verify.blacklist(src, "base"), [])


if __name__ == "__main__":
    unittest.main()
