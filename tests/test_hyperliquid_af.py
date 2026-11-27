"""Tests for findings/hyperliquid-assistance-fund-supply.

Every test works on a temporary copy of the case, so the committed data is never touched.

MUTATIONS holds input changes that must each turn one named row into FAIL (CONDITIONS records how
many conditions each row has). Every named condition has at least one mutation that breaks only
that condition within its row (test_every_condition_fails_alone checks this); where figures are
tied together (a total and its parts, a figure and its rounding), that mutation moves two inputs.
A few further mutations break several related conditions together. These tests exercise the
implemented comparisons; they do not establish that the saved responses are what the API
returned, or what the API's fields mean.
"""
import gzip
import hashlib
import json
import os
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import ROOT, CaseCopy, load_module, silent  # noqa: E402

CASE = ROOT / "findings" / "hyperliquid-assistance-fund-supply"
reproduce = load_module("reproduce_hyperliquid_af", CASE / "reproduce.py")
live_verify = load_module("live_verify_hyperliquid_af", CASE / "live_verify.py")
extract = load_module("extract_hyperliquid_af", CASE / "extract.py")

TD = reproduce.TOKEN_DETAILS
SPOT = reproduce.SPOT_AF
PUB = reproduce.PUBLIC
AF, ZERO, DEAD = reproduce.AF, reproduce.ZERO, reproduce.DEAD
BIG = "0x43e9abea1910387c4292bca4b94de81462f8a251"   # the largest non-circulating balance
OTHER = "0x" + "12" * 20
AF_NOW = "49345806.7147392333"
F = reproduce.FINDING


# --- mutation helpers -------------------------------------------------------------------------

def both(*fs):
    def f(d):
        for g in fs:
            g(d)
    return f


def set_field(field, value):
    return lambda td: td.update({field: value})


def add(field, delta):
    def f(td):
        td[field] = str(Decimal(td[field]) + Decimal(delta))
    return f


def entry(td, address):
    return next(row for row in td["nonCirculatingUserBalances"] if row[0] == address)


def add_balance(address, delta):
    def f(td):
        row = entry(td, address)
        row[1] = str(Decimal(row[1]) + Decimal(delta))
    return f


def set_af(value):
    def f(td):
        entry(td, AF)[1] = value
    return f


def set_spot_hype(value):
    def f(spot):
        next(b for b in spot["balances"] if b["coin"] == "HYPE")["total"] = value
    return f


def post_of(pub, pid):
    return next(p for p in pub["x_posts"] if p["id"] == pid)


def set_reader(pid, reader, key, value):
    def f(pub):
        post_of(pub, pid)["readers"][reader][key] = value
    return f


def unfind(pid, text, reader):
    def f(pub):
        next(q for q in post_of(pub, pid)["quotes"] if q["text"] == text)["found_by"][reader] = False
    return f


def set_cg(key, value):
    return lambda pub: pub["coingecko"]["fields"].update({key: value})


def add_cg(delta):
    def f(pub):
        fields = pub["coingecko"]["fields"]
        fields["market_data.total_supply"] = str(Decimal(fields["market_data.total_supply"]) + Decimal(delta))
    return f


def genesis(f):
    return lambda td: f(td["genesis"]["userBalances"])


def doc(pub, label):
    return next(d for d in pub["docs"] if d["label"] == label)


def first_genesis_plus(delta):
    def f(g):
        g[0][1] = str(Decimal(g[0][1]) + Decimal(delta))
    return f


PROP, RES = reproduce.PROPOSAL, reproduce.RESULT
Q1, Q2, Q3 = F["context_quotes"]

MUTATIONS = [
    # P1: proposal post
    ("P1", "syndication time", PUB, set_reader(PROP, "syndication", "created_at", "2025-12-17T03:10:34.000Z")),
    ("P1", "fxtwitter time", PUB, set_reader(PROP, "fxtwitter", "created_at", "Wed Dec 17 03:11:33 +0000 2025")),
    ("P1", "account", PUB, set_reader(PROP, "fxtwitter", "account", "someone")),
    ("P1", "link", PUB, lambda pub: post_of(pub, PROP).update(url=f"https://x.com/other/status/{PROP}")),
    ("P1", "sentence 1, full text", PUB, unfind(PROP, F["proposal_quote"], "fxtwitter")),
    ("P1", "sentence 1, short text", PUB, unfind(PROP, F["proposal_quote"], "syndication")),
    ("P1", "sentence 2, full text", PUB, unfind(PROP, F["no_onchain_quote"], "fxtwitter")),
    # P2: what the vote is
    ("P2", "treat as burned", PUB, unfind(PROP, Q1, "fxtwitter")),
    ("P2", "never authorize an upgrade", PUB, unfind(PROP, Q2, "fxtwitter")),
    ("P2", "never had a private key", PUB, unfind(PROP, Q3, "fxtwitter")),
    # P3: result post
    ("P3", "syndication time", PUB, set_reader(RES, "syndication", "created_at", "2025-12-24T04:28:49.000Z")),
    ("P3", "fxtwitter time", PUB, set_reader(RES, "fxtwitter", "created_at", "Wed Dec 24 05:28:48 +0000 2025")),
    ("P3", "account", PUB, set_reader(RES, "syndication", "account", "someone")),
    ("P3", "link", PUB, lambda pub: post_of(pub, RES).update(url=f"https://x.com/other/status/{RES}")),
    ("P3", "quote, syndication", PUB, unfind(RES, F["result_quote"], "syndication")),
    ("P3", "quote, fxtwitter", PUB, unfind(RES, F["result_quote"], "fxtwitter")),
    # P4: docs page
    ("P4", "page", PUB, lambda pub: doc(pub, "fees").update(url="https://hyperliquid.gitbook.io/hyperliquid-docs/other")),
    ("P4", "capture time", PUB, lambda pub: doc(pub, "fees").update(captured_utc="2026-11-28T05:51:13Z")),
    ("P4", "quote not found", PUB, lambda pub: doc(pub, "fees")["quotes"][0].update(found=False)),
    # P5: HIP-1
    ("P5", "page", PUB, lambda pub: doc(pub, "hip1").update(url="https://hyperliquid.gitbook.io/hyperliquid-docs/other")),
    ("P5", "capture time", PUB, lambda pub: doc(pub, "hip1").update(captured_utc="2026-11-28T06:45:04Z")),
    ("P5", "maxSupply quote not found", PUB, lambda pub: doc(pub, "hip1")["quotes"][0].update(found=False)),
    ("P5", "decrease quote not found", PUB, lambda pub: doc(pub, "hip1")["quotes"][1].update(found=False)),
    # R1: vote shares
    ("R1", "quote, syndication", PUB, unfind(RES, F["vote_quote"], "syndication")),
    ("R1", "quote, fxtwitter", PUB, unfind(RES, F["vote_quote"], "fxtwitter")),
    # G1: genesis
    ("G1", "total (still equal to maxSupply)", TD,
     both(genesis(first_genesis_plus("0.01")), set_field("maxSupply", "1000000000.01"))),
    ("G1", "total and its match to maxSupply", TD, genesis(first_genesis_plus("0.01"))),
    ("G1", "number of balances", TD, genesis(lambda g: g.append([OTHER, "0.0"]))),
    ("G1", "fund in genesis", TD, genesis(lambda g: g[0].__setitem__(0, AF))),
    ("G1", "total off maxSupply by 0.000002 (same cents)", TD, genesis(first_genesis_plus("0.000002"))),
    ("G1", "allocation through another token", TD,
     lambda td: td["genesis"]["existingTokenBalances"].append([1, "1000.0"])),
    ("G1", "a negative balance (total kept)", TD, genesis(lambda g: (g[0].__setitem__(1, str(Decimal(g[0][1]) - 5000)),
                                                                     g[1].__setitem__(1, str(Decimal(g[1][1]) + 5000))))),
    ("G1", "an address twice (total kept)", TD, genesis(lambda g: g[1].__setitem__(0, g[0][0]))),
    # L1: each figure alone (rounding edges keep the other two)
    ("L1", "maxSupply", TD, set_field("maxSupply", "999999999.994")),
    ("L1", "totalSupply", TD, both(set_field("maxSupply", "1000000000.004"), set_field("totalSupply", "998670601.385"))),
    ("L1", "gap", TD, set_field("maxSupply", "1000000000.004")),
    # L2: AF balance
    ("L2", "listed twice, same balance", TD, lambda td: td["nonCirculatingUserBalances"].append([AF, AF_NOW])),
    ("L2", "balance (both endpoints)", (TD, SPOT), (set_af("49345806.72"), set_spot_hype("49345806.72"))),
    ("L2", "spot state differs", SPOT, set_spot_hype("49345806.7147392334")),
    ("L2", "spot entry is another token", SPOT, lambda sp: next(b for b in sp["balances"] if b["coin"] == "HYPE").update(token=999)),
    ("L2", "not listed", TD, lambda td: td["nonCirculatingUserBalances"].remove(entry(td, AF))),
    # L3: circulating identity
    ("L3", "identity", TD, add("futureEmissions", "0.00001")),
    ("L3", "circulatingSupply (identity kept)", TD, both(add("circulatingSupply", "0.01"), add_balance(BIG, "-0.01"))),
    ("L3", "futureEmissions (identity kept)", TD, both(add("futureEmissions", "1"), add_balance(BIG, "-1"))),
    ("L3", "a fifth, zero balance", TD, lambda td: td["nonCirculatingUserBalances"].append([OTHER, "0.0"])),
    ("L3", "AF not among them", TD, lambda td: entry(td, AF).__setitem__(0, OTHER)),
    # L4: zero address and 0x…dead
    ("L4", "zero address balance", TD, both(add_balance(ZERO, "0.01"), add_balance(BIG, "-0.01"))),
    ("L4", "0x…dead balance", TD, both(add_balance(DEAD, "0.01"), add_balance(BIG, "-0.01"))),
    # A1: each operand alone
    ("A1", "AF balance", TD, set_af("49345806.715")),
    ("A1", "gap", TD, set_field("totalSupply", "998670601.375")),
    ("A1", "AF − gap", TD, both(set_af("49345806.714999"), set_field("totalSupply", "998670601.384999"))),
    ("A1", "fund outside totalSupply", TD, add("totalSupply", "-" + AF_NOW)),
    # X1: CoinGecko extract
    ("X1", "total_supply (differences kept)", (TD, PUB), (add("totalSupply", "1"), add_cg("1"))),
    ("X1", "difference to the API (AF difference kept)", TD, both(add("totalSupply", "1"), set_af("49345807.7147392333"))),
    ("X1", "difference to the AF balance", TD, set_af("49345807.7147392333")),
    ("X1", "last_updated earlier", PUB, set_cg("last_updated", "2026-11-26T09:52:00.000Z")),
    ("X1", "last_updated later", PUB, set_cg("last_updated", "2026-11-26T09:54:55.000Z")),
    ("X1", "last_updated later by a fraction of a second", PUB, set_cg("last_updated", "2026-11-26T09:54:54.999Z")),
    ("X1", "published flag", PUB, lambda pub: pub["coingecko"].update(response_published=True)),
]
CONDITIONS = {"P1": 7, "P2": 3, "P3": 6, "P4": 3, "P5": 4, "R1": 2, "G1": 7, "L1": 3, "L2": 4, "L3": 5, "L4": 2, "A1": 3,
              "X1": 5}


class Case(CaseCopy):
    CASE = CASE
    MODULE = reproduce

    def edit(self, case, rel, mutate):
        """JSON edits that keep the manifest and the recorded response hashes consistent, as a wrong but
        consistent dataset would be. The gzipped tokenDetails response is edited inside its archive.
        rel and mutate may be tuples, to change several files in one mutation."""
        if isinstance(rel, tuple):
            for r, m in zip(rel, mutate):
                self.edit(case, r, m)
            return
        p = case / rel
        if rel.endswith(".gz"):
            data = json.loads(gzip.decompress(p.read_bytes()))
            mutate(data)
            raw = json.dumps(data).encode()
            p.write_bytes(gzip.compress(raw, mtime=0))
        else:
            data = json.loads(p.read_text())
            mutate(data)
            raw = json.dumps(data, ensure_ascii=False).encode()
            p.write_bytes(raw)
        if rel in (TD, SPOT):
            pub_path = case / PUB
            pub = json.loads(pub_path.read_text())
            pub["info"]["response_sha256"][rel] = hashlib.sha256(raw).hexdigest()
            pub_path.write_text(json.dumps(pub, ensure_ascii=False))
        self.rehash(case)

    @staticmethod
    def rehash(case):
        manifest = json.loads((case / "MANIFEST.json").read_text())
        for f in manifest["files"]:
            q = case / f["path"]
            f["sha256"], f["bytes"] = reproduce.sha256(q), q.stat().st_size
        (case / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))


class Reproduce(Case):
    def test_exit_zero_and_no_fail(self):
        self.assertEqual(silent(reproduce.main, [], case=self.case), 0)
        self.assertFalse([r for r in reproduce.results(self.case) if r["status"] == "FAIL"])

    def test_statuses(self):
        got = {r["id"]: r["status"] for r in reproduce.results(self.case)}
        want = {k: "RECORDED" for k in ("P1", "P2", "P3", "P4", "P5", "R1", "X1")}
        want.update({k: "MATCH" for k in ("G1", "L1", "L2", "L3", "L4", "A1")})
        self.assertEqual(got, want)

    def test_runs_without_network(self):
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used")), \
             mock.patch("socket.socket", side_effect=AssertionError("network used")):
            self.assertEqual(silent(reproduce.main, [], case=self.case), 0)

    def test_two_decimals_round_half_up(self):
        self.assertEqual(reproduce.two(Decimal("1.005")), "1.01")
        self.assertEqual(reproduce.two(Decimal("1329398.6236718893")), "1,329,398.62")

    def test_both_x_timestamp_formats(self):
        self.assertEqual(reproduce.iso("2025-12-17T03:10:33.000Z"), "2025-12-17T03:10:33Z")
        self.assertEqual(reproduce.iso("Wed Dec 17 03:10:33 +0000 2025"), "2025-12-17T03:10:33Z")

    def test_post_ids_give_the_stated_times(self):
        self.assertEqual(reproduce.id_time(PROP), "2025-12-17T03:10:33.434Z")
        self.assertEqual(reproduce.id_time(RES), "2025-12-24T04:28:48.405Z")
        self.assertTrue(reproduce.id_time(PROP).startswith(F["proposal_utc"][:-1]))
        self.assertTrue(reproduce.id_time(RES).startswith(F["result_utc"][:-1]))

    def test_result_quote_names_the_fund_address(self):
        self.assertIn(AF, F["result_quote"])

    def test_coverage(self):
        self.check_coverage(MUTATIONS, CONDITIONS)

    def test_condition_counts_match_the_code(self):
        """CONDITIONS must equal the number of conditions each row really has, so coverage counts are exact."""
        seen = {}
        orig_check, orig_record = reproduce.Rows.check, reproduce.Rows.record

        def spy(fn):
            def g(self, cid, claim, stated, computed, level, conditions, note=""):
                seen[cid] = len(conditions)
                return fn(self, cid, claim, stated, computed, level, conditions, note)
            return g
        with mock.patch.object(reproduce.Rows, "check", spy(orig_check)), \
             mock.patch.object(reproduce.Rows, "record", spy(orig_record)):
            reproduce.results(self.case)
        self.assertEqual(seen, CONDITIONS)

    def test_each_mutation_fails_its_row(self):
        self.run_mutations(MUTATIONS)

    def test_every_condition_fails_alone(self):
        """For every named condition, some mutation breaks that condition and no other in its row."""
        broken_by = {}
        orig_check, orig_record = reproduce.Rows.check, reproduce.Rows.record

        def spy(fn, label):
            def g(self, cid, claim, stated, computed, level, conditions, note=""):
                broken_by.setdefault(cid, {})[label] = frozenset(k for k, v in conditions.items() if not v)
                return fn(self, cid, claim, stated, computed, level, conditions, note)
            return g
        conditions = {}

        def names(fn):
            def g(self, cid, claim, stated, computed, level, conds, note=""):
                conditions[cid] = set(conds)
                return fn(self, cid, claim, stated, computed, level, conds, note)
            return g
        with mock.patch.object(reproduce.Rows, "check", names(orig_check)), \
             mock.patch.object(reproduce.Rows, "record", names(orig_record)):
            reproduce.results(self.case)
        for i, (cid, label, rel, mutate) in enumerate(MUTATIONS):
            case = self.copy(f"iso{i}")
            self.edit(case, rel, mutate)
            with mock.patch.object(reproduce.Rows, "check", spy(orig_check, i)), \
                 mock.patch.object(reproduce.Rows, "record", spy(orig_record, i)):
                reproduce.results(case)
        for cid, conds in conditions.items():
            alone = {next(iter(b)) for b in broken_by.get(cid, {}).values() if len(b) == 1}
            with self.subTest(row=cid):
                self.assertEqual(conds - alone, set())

    def test_tampered_file_is_rejected(self):
        p = self.case / SPOT
        p.write_bytes(p.read_bytes().replace(b"HYPE", b"HYPF", 1))
        with self.assertRaises(reproduce.DataError):
            reproduce.results(self.case)
        self.assertEqual(silent(reproduce.main, [], case=self.case), 1)

    def test_public_sources_changed_without_manifest(self):
        p = self.case / PUB
        p.write_text(p.read_text().replace("HyperFND", "HyperFNX", 1))
        self.assertEqual(silent(reproduce.main, [], case=self.case), 1)

    def assert_capture_mismatch(self, rel, new_bytes):
        """A response changed and re-listed in the manifest, but not matching the hash recorded at capture."""
        (self.case / rel).write_bytes(new_bytes)
        self.rehash(self.case)
        with self.assertRaises(reproduce.DataError):
            reproduce.results(self.case)

    def test_spot_response_not_the_one_captured(self):
        self.assert_capture_mismatch(SPOT, (self.case / SPOT).read_bytes().replace(b"HYPE", b"HYPF", 1))

    def test_token_details_not_the_one_captured(self):
        raw = gzip.decompress((self.case / TD).read_bytes()).replace(b'"HYPE"', b'"HYPE" ', 1)
        self.assert_capture_mismatch(TD, gzip.compress(raw, mtime=0))

    def test_request_for_another_address(self):
        self.edit(self.case, PUB, lambda pub: pub["info"]["requests"][SPOT].update(user="0x" + "ab" * 20))
        with self.assertRaises(reproduce.DataError):
            reproduce.results(self.case)

    def test_request_for_another_token(self):
        self.edit(self.case, PUB, lambda pub: pub["info"]["requests"][TD].update(tokenId="0x" + "00" * 16))
        with self.assertRaises(reproduce.DataError):
            reproduce.results(self.case)

    def test_another_endpoint(self):
        self.edit(self.case, PUB, lambda pub: pub["info"].update(endpoint="https://example.org/info"))
        with self.assertRaises(reproduce.DataError):
            reproduce.results(self.case)

    def test_capture_time_differs(self):
        self.edit(self.case, PUB, lambda pub: pub["info"].update(captured_utc="2026-11-26T10:56:10Z"))
        with self.assertRaises(reproduce.DataError):
            reproduce.results(self.case)

    def test_capture_times_disagree(self):
        self.edit(self.case, PUB, lambda pub: pub["captured_utc"].update(info="2026-11-26T10:56:10Z"))
        with self.assertRaises(reproduce.DataError):
            reproduce.results(self.case)

    def test_token_details_for_another_token(self):
        self.edit(self.case, TD, lambda td: td.update(name="PURR"))
        with self.assertRaises(reproduce.DataError):
            reproduce.results(self.case)

    def test_not_a_number_is_a_data_error(self):
        for bad in ("abc", "NaN", "Infinity"):
            with self.subTest(value=bad):
                case = self.copy(f"bad_{bad}")
                self.edit(case, TD, set_field("totalSupply", bad))
                self.assertEqual(silent(reproduce.main, [], case=case), 1)

    def test_manifest_must_list_exactly_the_data_files(self):
        manifest = json.loads((self.case / "MANIFEST.json").read_text())
        manifest["files"].append(dict(manifest["files"][0]))
        (self.case / "MANIFEST.json").write_text(json.dumps(manifest))
        with self.assertRaises(reproduce.DataError):
            reproduce.results(self.case)

    def test_post_listed_twice_or_missing(self):
        for label, change in (("twice", lambda pub: pub["x_posts"].append(dict(pub["x_posts"][0]))),
                              ("missing", lambda pub: pub["x_posts"].pop(0))):
            with self.subTest(label):
                case = self.copy(f"post_{label}")
                self.edit(case, PUB, change)
                self.assertEqual(silent(reproduce.main, [], case=case), 1)

    def test_manifest_size_alone_differs(self):
        manifest = json.loads((self.case / "MANIFEST.json").read_text())
        manifest["files"][0]["bytes"] += 1
        (self.case / "MANIFEST.json").write_text(json.dumps(manifest))
        with self.assertRaises(reproduce.DataError):
            reproduce.results(self.case)

    def test_truncated_archive_is_a_data_error(self):
        p = self.case / TD
        p.write_bytes(p.read_bytes()[:-8])
        self.rehash(self.case)
        self.assertEqual(silent(reproduce.main, [], case=self.case), 1)

    def test_missing_file_is_rejected(self):
        (self.case / SPOT).unlink()
        self.assertEqual(silent(reproduce.main, [], case=self.case), 1)

    def test_unlisted_file_is_rejected(self):
        (self.case / "data" / "extra.json").write_text("{}")
        self.assertEqual(silent(reproduce.main, [], case=self.case), 1)


class Extract(unittest.TestCase):
    def test_page_text_finds_a_sentence_across_tags(self):
        page = b"<p>HYPE in the <b>assistance fund</b> is burned,\n removing</p>"
        self.assertIn("HYPE in the assistance fund is burned, removing", extract.page_text(page))

    def test_without_the_raw_responses_nothing_is_written(self):
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(silent(extract.main, ["--from", empty, "--check"]), 2)

    def test_constants_agree_with_reproduce(self):
        self.assertEqual(extract.DOCS_QUOTE, F["docs_quote"])
        self.assertEqual(extract.CAPTURED["info"], reproduce.CAPTURED)
        self.assertEqual(extract.CAPTURED["docs"], F["docs_utc"])
        self.assertEqual(extract.QUOTES[PROP][0], F["proposal_quote"])
        self.assertEqual(extract.HIP1_QUOTES, F["hip1_quotes"])
        self.assertEqual(extract.CAPTURED["hip1"], F["hip1_utc"])

    def test_page_text_ignores_scripts(self):
        page = b"<html><script>var s='" + F["docs_quote"].encode() + b"'</script><p>other</p></html>"
        self.assertNotIn(F["docs_quote"], extract.page_text(page))


DOCS_PAGE = ("<html><head><title>Fees | Hyperliquid Docs</title></head><p>"
             + F["docs_quote"].replace("assistance fund", "<b>assistance fund</b>") + "</p></html>").encode()


def saved_responses():
    td = gzip.decompress((CASE / TD).read_bytes())
    spot = (CASE / SPOT).read_bytes()
    return td, spot


def fake_post(td, spot):
    def post(body):
        return td if body["type"] == "tokenDetails" else spot
    return post


def fake_get(docs=DOCS_PAGE):
    def get(url):
        if url == reproduce.DOCS_URL:
            if isinstance(docs, Exception):
                raise docs
            return docs
        return b"{}"
    return get


class LiveVerify(unittest.TestCase):
    def run_check(self, td, spot, argv=("--check",), docs=DOCS_PAGE):
        return silent(live_verify.main, list(argv), post=fake_post(td, spot), get=fake_get(docs))

    def edited(self, change, spot_change=None):
        td, spot = saved_responses()
        d = json.loads(td)
        change(d)
        s = json.loads(spot)
        if spot_change:
            spot_change(s)
        return self.run_check(json.dumps(d).encode(), json.dumps(s).encode())

    def test_saved_state_holds(self):
        self.assertEqual(self.run_check(*saved_responses()), 0)

    def test_fund_taken_out_of_total_supply_is_1(self):
        self.assertEqual(self.edited(both(add("totalSupply", "-" + AF_NOW), add("circulatingSupply", "-" + AF_NOW))), 1)

    def test_vote_day_balance_taken_out_is_1(self):
        """Removing only the fund's balance on the day of the vote (37,547,309 HYPE) must not pass."""
        self.assertEqual(self.edited(both(add("totalSupply", "-37547309"), add("circulatingSupply", "-37547309"))), 1)

    def test_fund_no_longer_listed_is_1(self):
        self.assertEqual(self.edited(lambda d: d["nonCirculatingUserBalances"].remove(entry(d, AF))), 1)

    def test_other_token_is_1(self):
        self.assertEqual(self.edited(set_field("name", "PURR")), 1)

    def test_max_supply_changed_is_1(self):
        self.assertEqual(self.edited(set_field("maxSupply", "1000000001.0")), 1)

    def test_identity_broken_is_1(self):
        self.assertEqual(self.edited(add("circulatingSupply", "1")), 1)

    def test_total_above_max_with_identity_kept_is_1(self):
        self.assertEqual(self.edited(both(set_field("totalSupply", "1000000001.0"),
                                          add("circulatingSupply", "1329399.6236718893"))), 1)

    def test_new_supply_is_1(self):
        self.assertEqual(self.edited(both(add("totalSupply", "10"), add("circulatingSupply", "10"))), 1)

    def test_fund_below_snapshot_is_1(self):
        self.assertEqual(self.edited(both(set_af("49345805.7147392333"), add("circulatingSupply", "1")),
                                     set_spot_hype("49345805.7147392333")), 1)

    def test_negative_balance_is_1(self):
        self.assertEqual(self.edited(both(add_balance(DEAD, "-3"), add("circulatingSupply", "3"))), 1)

    def test_not_a_number_is_2(self):
        self.assertEqual(self.edited(set_field("totalSupply", "NaN")), 2)

    def test_drift_alone_is_inconclusive_2(self):
        self.assertEqual(self.edited(lambda d: None, set_spot_hype("49344806.7147392332")), 2)

    def test_drift_at_the_limit_holds(self):
        self.assertEqual(self.edited(lambda d: None, set_spot_hype("49344806.7147392333")), 0)

    def test_docs_sentence_missing_is_3(self):
        page = b"<html><head><title>Fees | Hyperliquid Docs</title></head><p>fees</p></html>"
        self.assertEqual(self.run_check(*saved_responses(), docs=page), 3)

    def test_docs_sentence_only_in_a_script_is_3(self):
        page = ("<html><head><title>Fees | Hyperliquid Docs</title></head><script>var s='" + F["docs_quote"]
                + "'</script><p>fees</p></html>").encode()
        self.assertEqual(self.run_check(*saved_responses(), docs=page), 3)

    def test_unidentified_page_is_4(self):
        self.assertEqual(self.run_check(*saved_responses(), docs=b"<html><h1>Access denied</h1></html>"), 4)

    def test_genesis_emptied_is_1(self):
        self.assertEqual(self.edited(lambda d: d.update(genesis={"userBalances": [], "existingTokenBalances": []})), 1)

    def test_fund_in_genesis_is_1(self):
        self.assertEqual(self.edited(lambda d: d["genesis"]["userBalances"][0].__setitem__(0, AF)), 1)

    def test_non_circulating_address_twice_is_1(self):
        self.assertEqual(self.edited(lambda d: d["nonCirculatingUserBalances"].append([ZERO, "0.0"])), 1)

    def test_spot_entry_for_another_token_is_2(self):
        self.assertEqual(self.edited(lambda d: None, lambda s: next(b for b in s["balances"] if b["coin"] == "HYPE")
                                     .update(token=999)), 2)

    def test_no_spot_entry_is_2(self):
        self.assertEqual(self.edited(lambda d: None, lambda s: s.update(balances=[])), 2)

    def test_snapshot_failing_its_manifest_is_2(self):
        with mock.patch.object(live_verify.reproduce, "verify_data", side_effect=live_verify.reproduce.DataError("x")):
            self.assertEqual(self.run_check(*saved_responses()), 2)

    def test_docs_unreadable_is_4(self):
        self.assertEqual(self.run_check(*saved_responses(), docs=OSError("no route")), 4)

    def test_failed_read_is_2(self):
        def broken(body):
            raise OSError("no route")
        self.assertEqual(silent(live_verify.main, ["--check"], post=broken, get=fake_get()), 2)

    def test_unreadable_answer_is_2(self):
        self.assertEqual(self.run_check(b"<html>", b"{}"), 2)

    def test_coingecko_never_changes_the_exit(self):
        self.assertEqual(self.run_check(*saved_responses(), argv=("--check", "--coingecko")), 0)

    def test_save_refuses_data_dir(self):
        self.assertEqual(self.run_check(*saved_responses(), argv=("--check", "--save", str(CASE / "data"))), 2)

    def test_save_refuses_a_link_into_data(self):
        with tempfile.TemporaryDirectory() as out:
            os.symlink(CASE / "data" / "never_written.json", Path(out) / "info_tokenDetails.json")
            self.assertEqual(self.run_check(*saved_responses(), argv=("--check", "--save", out)), 2)
        self.assertFalse((CASE / "data" / "never_written.json").exists())

    def test_save_never_overwrites(self):
        """An existing name may be a hard link to a file elsewhere; it is refused, not written through."""
        with tempfile.TemporaryDirectory() as out:
            victim = Path(out) / "victim.json"
            victim.write_text("keep")
            os.link(victim, Path(out) / "info_tokenDetails.json")
            self.assertEqual(self.run_check(*saved_responses(), argv=("--check", "--save", out)), 2)
            self.assertEqual(victim.read_text(), "keep")

    def test_save_onto_a_file_is_2(self):
        with tempfile.NamedTemporaryFile() as f:
            self.assertEqual(self.run_check(*saved_responses(), argv=("--check", "--save", f.name)), 2)

    def test_save_elsewhere(self):
        td, spot = saved_responses()
        with tempfile.TemporaryDirectory() as out:
            self.assertEqual(self.run_check(td, spot, ("--check", "--save", out)), 0)
            self.assertEqual((Path(out) / "info_tokenDetails.json").read_bytes(), td)
            self.assertEqual((Path(out) / "docs_trading_fees.html").read_bytes(), DOCS_PAGE)


if __name__ == "__main__":
    unittest.main()
