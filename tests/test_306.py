"""Tests for findings/306-sky-atlas-burn.

Every test works on a temporary copy of the case, so the committed data is never touched.

MUTATIONS holds input changes that must each turn one named row into FAIL, at least one per
condition the row checks (CONDITIONS records how many each row needs). Where it can be done,
a mutation breaks only the condition it names. The erratum gets both directions: "the documented
correction no longer holds" and "the report's statement has become true".
"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import ROOT, CaseCopy, call, load_module, query, set_words, silent, word  # noqa: E402

CASE = ROOT / "findings" / "306-sky-atlas-burn"
reproduce = load_module("reproduce_306", CASE / "reproduce.py")
live_verify = load_module("live_verify_306", CASE / "live_verify.py")

SNAP = "data/live_snapshot.json"
BIG = "data/window_b_buybacks.json"
B_START, CAST, A_END = reproduce.B_START, reproduce.CAST, reproduce.A_END
_saved = json.loads((CASE / SNAP).read_text())
HEAD = _saved["pinned_head"]
AUG, SEP = _saved["end_of_august_block"], _saved["end_of_september_block"]
BURNS_B = "SKY Transfer to 0x0, window B"
MINTS_B = "SKY Transfer from 0x0, window B"
KICKS_A = "USDS MCD_FLAP -> pair, window A"
BOUGHT_A = "SKY pair -> MCD_PAUSE_PROXY, window A"
KICKS_S = "USDS MCD_FLAP -> pair, scale range"
BOUGHT_S = "SKY pair -> MCD_PAUSE_PROXY, scale range"
BURNS_AFTER = "SKY Transfer to 0x0, cast to pinned head"
BOUGHT_AUG = "SKY pair -> MCD_PAUSE_PROXY, cast to end of August"
BOUGHT_SEP = "SKY pair -> MCD_PAUSE_PROXY, September"
KICKS_AUG = "USDS MCD_FLAP -> pair, cast to end of August"
KICKS_SEP = "USDS MCD_FLAP -> pair, September"
MINTS_AFTER = "SKY Transfer from 0x0, cast to pinned head"
OTHER = "0x" + "0" * 24 + "77" * 20
NEW_TX = "0x" + "cd" * 32
SKY = 10 ** 18
LAST_B_BURN = 24246170


def rows(d, label):
    q = query(d, label)
    return q["rows"], q["fields"]


def setnum(label, f):
    def mutate(d):
        c = call(d, label)
        c["result"] = word(f(int(c["result"], 16)))
    return mutate


def block_time(block, f):
    def mutate(d):
        d["blocks"][str(block)]["timestamp"] = f(d["blocks"][str(block)]["timestamp"])
    return mutate


def bump(label, delta, i=0):
    def mutate(d):
        rs, fs = rows(d, label)
        rs[i][fs.index("data")] = set_words(rs[i][fs.index("data")], {0: lambda v: v + delta})
    return mutate


def add_row(label, amount=0, tx=NEW_TX, block=None):
    def mutate(d):
        rs, fs = rows(d, label)
        r = list(rs[0])
        r[fs.index("transactionHash")] = tx
        r[fs.index("logIndex")] += 1000
        if block is not None:
            r[fs.index("blockNumber")] = block
        r[fs.index("data")] = set_words(r[fs.index("data")], {0: lambda v: amount})
        rs.append(r)
    return mutate


def retx(label, i=0):
    def mutate(d):
        rs, fs = rows(d, label)
        rs[i][fs.index("transactionHash")] = NEW_TX
    return mutate


def split_burn(d):
    rs, fs = rows(d, BURNS_B)
    r = rs[-1]
    extra = list(r)
    extra[fs.index("logIndex")] += 1000
    extra[fs.index("data")] = set_words(r[fs.index("data")], {0: lambda v: 500})
    r[fs.index("data")] = set_words(r[fs.index("data")], {0: lambda v: v - 500})
    rs.append(extra)


def burn_into_window_a(d):
    rs, fs = rows(d, BURNS_B)
    rs[0][fs.index("blockNumber")] = CAST + 1
    d["blocks"][str(CAST + 1)] = d["blocks"][str(rs[1][fs.index("blockNumber")])]


def other_sender(d):
    rs, fs = rows(d, BURNS_B)
    topics = list(rs[0][fs.index("topics")])
    topics[1] = "0x" + "0" * 24 + "77" * 20
    rs[0][fs.index("topics")] = topics


def swap_amounts(d):
    rs, fs = rows(d, BURNS_B)
    i = fs.index("data")
    rs[0][i], rs[-1][i] = rs[-1][i], rs[0][i]


def add_mint(d):
    q = query(d, MINTS_B)
    fs = q["fields"]
    r = {"blockNumber": B_START + 1, "logIndex": 0, "transactionHash": NEW_TX,
         "topics": [q["topics"][0], q["topics"][1], "0x" + "0" * 24 + "77" * 20], "data": "0x" + "0" * 64}
    q["rows"].append([r[f] for f in fs])


def zero_kick_at_end(usds_label, sky_label):
    """One more kick of zero at the end of a month window: the same total, so a second run of kicks also matches."""
    def mutate(d):
        for label in (usds_label, sky_label):
            rs, fs = rows(d, label)
            r = list(rs[-1])
            r[fs.index("transactionHash")] = NEW_TX
            r[fs.index("logIndex")] += 1000
            r[fs.index("data")] = set_words(r[fs.index("data")], {0: lambda v: 0})
            rs.append(r)
    return mutate


def mint_after(d):
    q = query(d, MINTS_AFTER)
    r = {"blockNumber": CAST + 1, "logIndex": 0, "transactionHash": NEW_TX,
         "topics": [q["topics"][0], q["topics"][1], OTHER], "data": "0x" + "0" * 64}
    q["rows"].append([r[f] for f in q["fields"]])
    d["blocks"].setdefault(str(CAST + 1), d["blocks"][str(CAST)])


def setcall(label, value):
    def mutate(d):
        call(d, label)["result"] = value
    return mutate


def chain(*fs):
    def mutate(d):
        for f in fs:
            f(d)
    return mutate


def setpub(path, value):
    def mutate(d):
        node = d["public"]
        for k in path[:-1]:
            node = node[k]
        node[path[-1]] = value
    return mutate


def third_burn(d):
    rs, fs = rows(d, BURNS_AFTER)
    r = list(rs[-1])
    r[fs.index("transactionHash")] = NEW_TX
    r[fs.index("logIndex")] += 1000
    r[fs.index("data")] = set_words(r[fs.index("data")], {0: lambda v: 0})
    rs.append(r)


def burn_by_other(d):
    rs, fs = rows(d, BURNS_AFTER)
    topics = list(rs[0][fs.index("topics")])
    topics[1] = "0x" + "0" * 24 + "77" * 20
    rs[0][fs.index("topics")] = topics


MUTATIONS = [
    ("L1", "cast time", SNAP, block_time(CAST, lambda t: t + 1)),
    ("L1", "end time", SNAP, block_time(A_END, lambda t: t + 1)),
    ("L2", "17 kicks, same total", SNAP, add_row(KICKS_A)),
    ("L2", "total", SNAP, bump(KICKS_A, 1)),
    ("L3", "SKY total", SNAP, bump(BOUGHT_A, SKY // 100)),
    ("L3", "17 paired logs", SNAP, chain(add_row(KICKS_A), add_row(BOUGHT_A))),
    ("L3", "a SKY log on another transaction", SNAP, retx(BOUGHT_A)),
    ("L4", "a burn inside window A", SNAP, burn_into_window_a),
    ("L5", "supply at the cast", SNAP, setnum(f"SKY.totalSupply()@{CAST}", lambda v: v + 1)),
    ("L5", "supply at the end", SNAP, setnum(f"SKY.totalSupply()@{A_END}", lambda v: v + 1)),
    ("L6", "0x0 balance", SNAP, setnum(f"SKY.balanceOf({reproduce.HOLDERS[0]})@{A_END}", lambda v: v + 1)),
    ("L6", "dEaD balance", SNAP, setnum(f"SKY.balanceOf({reproduce.HOLDERS[1]})@{A_END}", lambda v: v + 1)),
    ("L6", "0x…0001 balance", SNAP, setnum(f"SKY.balanceOf({reproduce.HOLDERS[2]})@{A_END}", lambda v: v + 1)),
    ("L7", "start a day earlier, still 395 days", SNAP, block_time(B_START, lambda t: t - t % 86400 - 1)),
    ("L7", "end a day later, still 395 days", SNAP, block_time(A_END, lambda t: t - t % 86400 + 86401)),
    ("L7", "394 days, same calendar days", SNAP, block_time(B_START, lambda t: t - t % 86400 + 86399)),
    ("L8", "four burns, same total", SNAP, split_burn),
    ("L8", "one wei more burned, end supply to match", SNAP,
     chain(bump(BURNS_B, 1), setnum(f"SKY.totalSupply()@{A_END}", lambda v: v - 1))),
    ("L8", "a mint of zero", SNAP, add_mint),
    ("L8", "net against the endpoints", SNAP, setnum(f"SKY.totalSupply()@{A_END}", lambda v: v + 1)),
    ("L8", "start supply, difference kept", SNAP,
     chain(setnum(f"SKY.totalSupply()@{B_START}", lambda v: v + 1), setnum(f"SKY.totalSupply()@{A_END}", lambda v: v + 1))),
    ("L9", "a burn on another day", SNAP, block_time(LAST_B_BURN, lambda t: t + 86400)),
    ("L9", "a second sender", SNAP, other_sender),
    ("L9", "amounts in another order", SNAP, swap_amounts),
    ("L10", "correction: span one second longer", SNAP, block_time(LAST_B_BURN, lambda t: t + 1)),
    ("L10", "correction: a fourth burn", SNAP, split_burn),
    ("L10", "statement: span becomes 18 minutes", SNAP, block_time(LAST_B_BURN, lambda t: t - 36)),
    ("L11", "one more pair of kicks", BIG, chain(add_row(KICKS_S), add_row(BOUGHT_S))),
    ("L11", "SKY total", BIG, bump(BOUGHT_S, SKY // 100)),
    ("L11", "a SKY log on another transaction", BIG, retx(BOUGHT_S)),
    ("L11", "another flapper at the start of the range", SNAP, setcall(f"chainlog MCD_FLAP@{B_START}", OTHER)),
    ("L11", "another pair at the start of the range", SNAP, setcall(f"MCD_FLAP.pair()@{B_START}", OTHER)),
    ("L12", "split", SNAP, setnum("MCD_SPLIT.burn()", lambda v: v + 10 ** 16)),
    ("A1", "implied burn", SNAP, bump(BOUGHT_A, SKY)),
    ("P1", "source of another address", SNAP, setpub(["sky_sol", "address"], "0x" + "11" * 20)),
    ("P1", "two source files", SNAP, lambda d: d["public"]["sky_sol"]["source_files"].append("src/Other.sol")),
    ("P1", "no Sourcify match", SNAP, setpub(["sky_sol", "sourcify_match", "runtimeMatch"], None)),
    ("P1", "a third write", SNAP, lambda d: d["public"]["sky_sol"]["totalSupply_writes"].append("totalSupply = 0;")),
    ("P1", "mint does not add", SNAP, lambda d: d["public"]["sky_sol"]["totalSupply_writes"].__setitem__(0, "totalSupply = value;")),
    ("P1", "burn does not subtract", SNAP, lambda d: d["public"]["sky_sol"]["totalSupply_writes"].__setitem__(1, "totalSupply = value;")),
    ("P1", "mint emits elsewhere", SNAP, lambda d: d["public"]["sky_sol"]["next_emit_after_each_write"].__setitem__(0, "emit Mint(to, value);")),
    ("P1", "burn emits elsewhere", SNAP, lambda d: d["public"]["sky_sol"]["next_emit_after_each_write"].__setitem__(1, "emit Burn(from, value);")),
    ("P2", "A.2 clause number", SNAP, setpub(["atlas_at_report", "clauses", "A.2.3.1.2.4", "document_number"], "A.2.3.1.2.5")),
    ("P2", "A.4 clause number", SNAP, setpub(["atlas_at_report", "clauses", "A.4.1.2.2.3", "document_number"], None)),
    ("P2", "A.2 wording", SNAP, setpub(["atlas_at_report", "clauses", "A.2.3.1.2.4", "quotes_present"], [False])),
    ("P2", "A.4 wording", SNAP, setpub(["atlas_at_report", "clauses", "A.4.1.2.2.3", "quotes_present"], [True, True, False])),
    ("P2", "A.2 name", SNAP, setpub(["atlas_at_report", "clauses", "A.2.3.1.2.4", "name"], "Step 3")),
    ("P2", "A.4 name", SNAP, setpub(["atlas_at_report", "clauses", "A.4.1.2.2.3", "name"], "Burning")),
    ("P2", "A.2 not Core", SNAP, setpub(["atlas_at_report", "clauses", "A.2.3.1.2.4", "type"], "Section")),
    ("P2", "A.4 not Core", SNAP, setpub(["atlas_at_report", "clauses", "A.4.1.2.2.3", "type"], "Section")),
    ("P2", "A.4 does not refer to A.2", SNAP, setpub(["atlas_at_report", "clauses", "A.4.1.2.2.3", "refers_to"], [])),
    ("P5", "not found in July", SNAP, setpub(["atlas_mid_july", "clauses", "A.4.1.2.2.3", "document_number"], None)),
    ("P5", "other wording in July", SNAP, setpub(["atlas_mid_july", "clauses", "A.4.1.2.2.3", "quotes_present"], [True, False, True])),
    ("P3", "'non-issuance' found", SNAP, setpub(["atlas_at_report", "absent_phrase_counts", "non-issuance"], 1)),
    ("P3", "'not issued' found", SNAP, setpub(["atlas_at_report", "absent_phrase_counts", "not issued"], 1)),
    ("P3", "'no explicit burn' found", SNAP, setpub(["atlas_at_report", "absent_phrase_counts", "no explicit burn"], 1)),
    ("P3", "'reduce the supply' found", SNAP, setpub(["atlas_at_report", "absent_phrase_counts", "reduce the supply"], 1)),
    ("P3", "the search sees nothing", SNAP, setpub(["atlas_at_report", "present_phrase_counts", "USDS"], 0)),
    ("P4", "no 'no explicit burn'", SNAP, setpub(["forum_28153_post_1", "quotes_present"], [False, True])),
    ("P4", "no 'non-issuance'", SNAP, setpub(["forum_28153_post_1", "quotes_present"], [True, False])),
    ("O1", "reply says otherwise", SNAP, setpub(["maintainer_reply", "says_burned_in_future_executive_vote"], False)),
    ("O1", "reply asks for an Atlas change", SNAP, setpub(["maintainer_reply", "says_no_atlas_change"], False)),
    ("O1", "issue open", SNAP, setpub(["issue_306", "state"], "open")),
    ("O1", "closed at another time", SNAP, setpub(["issue_306", "closed_at"], "2026-08-19T00:00:00Z")),
    ("O2", "a third burn of zero", SNAP, third_burn),
    ("O2", "a burn by another holder", SNAP, burn_by_other),
    ("O2", "supply now", SNAP, setnum(f"SKY.totalSupply()@{HEAD}", lambda v: v + 1)),
    ("O2", "a mint of zero", SNAP, mint_after),
    ("O3", "August buybacks six SKY higher (1.09 SKY at 10/55)", SNAP, bump(BOUGHT_AUG, 6 * SKY)),
    ("O3", "an August SKY log on another transaction", SNAP, retx(BOUGHT_AUG, -1)),
    ("O3", "window end is not the month's last block", SNAP, block_time(AUG + 1, lambda t: t - 13)),
    ("O3", "burn on another day", SNAP, lambda d: block_time(rows(d, BURNS_AFTER)[0][0][0], lambda t: t + 86400)(d)),
    ("O3", "a second matching run", SNAP, zero_kick_at_end(KICKS_AUG, BOUGHT_AUG)),
    ("O3", "window A outside the window", SNAP, retx(BOUGHT_A)),
    ("O4", "September buybacks ten SKY higher", SNAP, bump(BOUGHT_SEP, 10 * SKY)),
    ("O4", "a September SKY log on another transaction", SNAP, retx(BOUGHT_SEP, -1)),
    ("O4", "window end is not the month's last block", SNAP, block_time(SEP + 1, lambda t: t - 13)),
    ("O4", "burn on another day", SNAP, lambda d: block_time(rows(d, BURNS_AFTER)[0][1][0], lambda t: t + 86400)(d)),
    ("O4", "a second matching run", SNAP, zero_kick_at_end(KICKS_SEP, BOUGHT_SEP)),
    ("O5", "A.2 wording changed", SNAP, setpub(["atlas_now", "clauses", "A.2.3.1.2.4", "quotes_present"], [True, False])),
    ("O5", "A.4 wording changed", SNAP, setpub(["atlas_now", "clauses", "A.4.1.2.2.3", "quotes_present"], [True, True, False])),
    ("O5", "A.2 clause renumbered", SNAP, setpub(["atlas_now", "clauses", "A.2.3.1.2.4", "document_number"], "A.2.3.1.2.5")),
    ("O5", "A.4 clause renumbered", SNAP, setpub(["atlas_now", "clauses", "A.4.1.2.2.3", "document_number"], "A.4.1.2.2.4")),
    ("O5", "A.2 renamed", SNAP, setpub(["atlas_now", "clauses", "A.2.3.1.2.4", "name"], "Step 3")),
    ("O5", "A.4 renamed", SNAP, setpub(["atlas_now", "clauses", "A.4.1.2.2.3", "name"], "Burning")),
    ("O5", "A.2 no longer Core", SNAP, setpub(["atlas_now", "clauses", "A.2.3.1.2.4", "type"], "Section")),
    ("O5", "A.4 no longer Core", SNAP, setpub(["atlas_now", "clauses", "A.4.1.2.2.3", "type"], "Section")),
]

CONDITIONS = {"L1": 2, "L2": 2, "L3": 3, "L5": 2, "L6": 3, "L7": 3, "L8": 5, "L9": 3, "L10": 3, "L11": 5,
              "P1": 8, "P2": 9, "P3": 5, "P4": 2, "P5": 2, "O1": 4, "O2": 4, "O3": 6, "O4": 5, "O5": 8}
NOT_DATA = {"R1"}  # the report's own control counts: no condition
ERRATA = {"L10"}


class Base(CaseCopy):
    CASE = CASE
    MODULE = reproduce


class TestPristine(Base):
    def test_exit_zero_and_no_fail(self):
        self.assertEqual(silent(reproduce.main, [], case=self.case), 0)
        self.assertFalse([r["id"] for r in reproduce.results(self.case) if r["status"] == "FAIL"])

    def test_statuses(self):
        rows = reproduce.results(self.case)
        by = lambda s: {r["id"] for r in rows if r["status"] == s}
        self.assertEqual(by("ERRATUM"), ERRATA)
        self.assertEqual(by("OBSERVED"), {"O1", "O2", "O3", "O4", "O5"})
        self.assertEqual(by("RECORDED"), {"R1"})

    def test_runs_without_network(self):
        import socket

        def refuse(*a, **k):
            raise AssertionError("reproduce.py must not open a network connection")

        with mock.patch.object(socket, "socket", refuse), mock.patch.object(socket, "create_connection", refuse):
            self.assertEqual(silent(reproduce.main, [], case=self.case), 0)

    def test_two_decimals_round_half_up(self):
        from fractions import Fraction
        self.assertEqual(reproduce.two(Fraction(976150616825077700000000, 10 ** 18)), "976,150.62")
        self.assertEqual(reproduce.two(Fraction(5, 1000)), "0.01")
        self.assertEqual(reproduce.two(Fraction(4999, 1000000)), "0.00")


class TestEveryCondition(Base):
    def test_coverage(self):
        self.check_coverage(MUTATIONS, CONDITIONS, ERRATA, not_data=NOT_DATA)

    def test_each_mutation_fails_its_row(self):
        self.run_mutations(MUTATIONS, ERRATA)


class TestStructure(Base):
    """A read that is not what its label says is a data error, not a row result."""

    def expect_error(self, rel, mutate, pattern):
        self.edit(self.case, rel, mutate)
        with self.assertRaisesRegex(reproduce.DataError, pattern):
            reproduce.results(self.case)

    def test_call_at_another_block(self):
        self.expect_error(SNAP, lambda d: call(d, "MCD_SPLIT.burn()").__setitem__("block", A_END - 1), "not the call")

    def test_query_over_another_range(self):
        self.expect_error(SNAP, lambda d: query(d, KICKS_A).__setitem__("from", CAST + 1), "not the query")

    def test_scale_query_with_another_filter(self):
        self.expect_error(BIG, lambda d: query(d, KICKS_S)["topics"].__setitem__(2, "0x" + "0" * 64), "not the query")

    def test_log_outside_its_filter(self):
        def other_from(d):
            rs, fs = rows(d, BURNS_B)
            topics = list(rs[0][fs.index("topics")])
            topics[2] = "0x" + "0" * 24 + "77" * 20
            rs[0][fs.index("topics")] = topics
        self.expect_error(SNAP, other_from, "outside its filter")

    def test_address_not_from_the_chainlog(self):
        self.expect_error(SNAP, lambda d: d["addresses"].__setitem__("MCD_SPLIT", "0x" + "11" * 20), "chainlog")


class TestIntegrity(Base):
    def test_tampered_file_is_rejected(self):
        p = self.case / BIG
        b = bytearray(p.read_bytes())
        b[500] = ord("7") if b[500] != ord("7") else ord("8")
        p.write_bytes(bytes(b))
        self.assertEqual(silent(reproduce.main, [], case=self.case), 1)

    def test_missing_file_is_rejected(self):
        (self.case / BIG).unlink()
        with self.assertRaisesRegex(reproduce.DataError, "missing"):
            reproduce.results(self.case)

    def test_unlisted_file_is_rejected(self):
        (self.case / "data/extra.json").write_text("{}")
        with self.assertRaisesRegex(reproduce.DataError, "not in MANIFEST"):
            reproduce.results(self.case)


class TestLiveCheck(Base):
    """live_verify.py --check against stand-ins for the archive node and the public sources."""

    def run_check(self, fresh=None, big=None, public=None, argv=("--check",)):
        snap = json.loads((self.case / SNAP).read_text())
        hist = fresh or {k: snap[k] for k in live_verify.HISTORICAL}
        stored = big or json.loads((self.case / BIG).read_text())
        seen = {}

        def read_chain(r, now):
            seen["now"] = now
            return hist, stored

        def public_state():
            if isinstance(public, Exception):
                raise public
            return public or snap["public"]

        with mock.patch.object(live_verify, "SNAPSHOT", self.case / SNAP), \
                mock.patch.object(live_verify, "BUYBACKS", self.case / BIG), \
                mock.patch.object(live_verify, "archive_checks", lambda rpc: snap["archive_checks"]), \
                mock.patch.object(live_verify, "read_chain", read_chain), \
                mock.patch.object(live_verify, "public_state", public_state):
            code = silent(live_verify.main, list(argv), rpc=object(), logs=object())
        if "--check" in argv:
            self.assertEqual(seen.get("now"), snap["pinned_head"])
        return code

    def test_identical_is_0(self):
        self.assertEqual(self.run_check(), 0)

    def test_historical_difference_is_1(self):
        snap = json.loads((self.case / SNAP).read_text())
        hist = {k: snap[k] for k in live_verify.HISTORICAL}
        hist["calls"][0]["result"] = word(1)
        self.assertEqual(self.run_check(fresh=hist), 1)

    def test_buyback_difference_is_1(self):
        big = json.loads((self.case / BIG).read_text())
        big["logs"][0]["rows"].pop()
        self.assertEqual(self.run_check(big=big), 1)

    def test_public_change_is_3(self):
        public = json.loads((self.case / SNAP).read_text())["public"]
        public["atlas_now"]["clauses"]["A.2.3.1.2.4"]["quotes_present"] = [False]
        self.assertEqual(self.run_check(public=public), 3)

    def test_public_failure_still_checks_history(self):
        self.assertEqual(self.run_check(public=RuntimeError("rate limited")), 4)

    def test_write_refuses_partial_snapshot(self):
        before = (self.case / SNAP).read_bytes(), (self.case / BIG).read_bytes()
        with mock.patch.object(live_verify, "SNAPSHOT", self.case / SNAP), \
                mock.patch.object(live_verify, "BUYBACKS", self.case / BIG), \
                mock.patch.object(live_verify, "archive_checks", lambda rpc: {"pass": True}), \
                mock.patch.object(live_verify, "read_chain", lambda r, now: ({}, {"logs": []})), \
                mock.patch.object(live_verify, "public_state", mock.Mock(side_effect=RuntimeError("down"))):

            class Head:
                def request(self, method, params):
                    return hex(HEAD + 64)
            self.assertEqual(silent(live_verify.main, ["--write"], rpc=Head(), logs=object()), 4)
        self.assertEqual(((self.case / SNAP).read_bytes(), (self.case / BIG).read_bytes()), before)

    def test_written_rows_read_back(self):
        big = json.loads((self.case / BIG).read_text())
        out = self.tmp / "rows.json"
        live_verify.write_rows(out, big)
        self.assertEqual(json.loads(out.read_text()), big)


class TestAtlasParsing(unittest.TestCase):
    def test_clause_by_front_matter(self):
        files = {"content/x/document.md": "---\nid: 5ce73730-4d5d-479c-b01e-40e87f072121\ndocNo: A.2.3.1.2.4\n---\n# A.2.3.1.2.4 - Step 3\nbody text\n"}
        files["content/x/document.md"] = files["content/x/document.md"].replace("docNo:", "name: Step 3\ntype: Core\ndocNo:")
        no, name, kind, body = live_verify.clause(files, "5ce73730-4d5d-479c-b01e-40e87f072121")
        self.assertEqual((no, name, kind), ("A.2.3.1.2.4", "Step 3", "Core"))
        self.assertIn("body text", body)

    def test_clause_by_heading_stops_at_next_clause(self):
        text = ("## A.2.3.1.2.4 - Step 3 [Core]  <!-- UUID: 5ce73730-4d5d-479c-b01e-40e87f072121 -->\n\nmine\n\n"
                "### A.2.3.1.2.5 - Next [Core]  <!-- UUID: 00000000-0000-0000-0000-000000000000 -->\n\nnot mine\n")
        no, name, kind, body = live_verify.clause({"content/A.2.md": text}, "5ce73730-4d5d-479c-b01e-40e87f072121")
        self.assertEqual((no, name, kind), ("A.2.3.1.2.4", "Step 3", "Core"))
        self.assertIn("mine", body)
        self.assertNotIn("not mine", body)

    def test_unknown_uuid(self):
        self.assertEqual(live_verify.clause({"a.md": "# x"}, "5ce73730-4d5d-479c-b01e-40e87f072121"), (None, None, None, None))


if __name__ == "__main__":
    unittest.main()
