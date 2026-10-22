"""Tests for findings/8812-defillama-rocketpool-megapools.

Every test works on a temporary copy of the case, so the committed data is never touched.

MUTATIONS holds input changes that must each turn one named row into FAIL, at least one per
condition the row checks (CONDITIONS records how many each row needs). Where it can be done,
a mutation breaks only the condition it names, so that no condition is covered by another.
"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import ROOT, CaseCopy, call, load_module, query, set_words, silent, word  # noqa: E402

CASE = ROOT / "findings" / "8812-defillama-rocketpool-megapools"
reproduce = load_module("reproduce_8812", CASE / "reproduce.py")
live_verify = load_module("live_verify_8812", CASE / "live_verify.py")

SNAP = "data/live_snapshot.json"
RD = "RewardsDistributed, any emitter"
OTHER = "0x34558f6ec05f91773b7d269f50ce0bbec4403760"
VAULT_IN = "rocketVault EtherDeposited by rocketRewardsPool"
VAULT_OUT = "rocketVault EtherWithdrawn by rocketRewardsPool"
_saved = json.loads((CASE / SNAP).read_text())
MEGAPOOLS = sorted(c["label"][len("megapool.exists("):-1] for c in _saved["calls"]
                   if c["label"].startswith("megapool.exists(") and int(c["result"], 16) == 1)
MP = MEGAPOOLS[0]
DEP = f"rETH EtherDeposited from {MP}"
NEW_TX = "0x" + "ab" * 32


def rows(d, label):
    q = query(d, label)
    return q["rows"], q["fields"]


def setcall(label, value=None, reverted=False):
    def mutate(d):
        c = call(d, label)
        old = int(c["result"], 16) if "result" in c else 0
        c.pop("result", None)
        c.pop("reverted", None)
        if reverted:
            c["reverted"] = True
        else:
            c["result"] = value(old) if callable(value) else value
    return mutate


def setnum(label, f):
    return setcall(label, lambda v: word(f(v)))


def first_mp_rd(d):
    rs, fs = rows(d, RD)
    return next(r for r in rs if r[fs.index("address")] == MP), fs


def first_other_rd(d):
    rs, fs = rows(d, RD)
    return next(r for r in rs if r[fs.index("address")] == OTHER), fs


def bump_dep(delta):
    def mutate(d):
        rs, fs = rows(d, DEP)
        rs[0][fs.index("data")] = set_words(rs[0][fs.index("data")], {0: lambda v: v + delta})
    return mutate


def bump_rd(index, delta, who=first_mp_rd):
    def mutate(d):
        r, fs = who(d)
        r[fs.index("data")] = set_words(r[fs.index("data")], {index: lambda v: v + delta})
    return mutate


def chain(*fs):
    def mutate(d):
        for f in fs:
            f(d)
    return mutate


def split_dep(d):
    rs, fs = rows(d, DEP)
    r = rs[0]
    amount = int(r[fs.index("data")][2:66], 16)
    extra = list(r)
    extra[fs.index("logIndex")] = r[fs.index("logIndex")] + 1000
    extra[fs.index("data")] = set_words(r[fs.index("data")], {0: lambda v: 1})
    r[fs.index("data")] = set_words(r[fs.index("data")], {0: lambda v: amount - 1})
    rs.append(extra)


def split_rd(d):
    r, fs = first_mp_rd(d)
    extra = list(r)
    extra[fs.index("logIndex")] = r[fs.index("logIndex")] + 1000
    extra[fs.index("data")] = set_words(r[fs.index("data")], {0: lambda v: 0, 1: lambda v: 0, 2: lambda v: 1, 3: lambda v: 0})
    r[fs.index("data")] = set_words(r[fs.index("data")], {2: lambda v: v - 1})
    rows(d, RD)[0].append(extra)


def drop_pair(d):
    deps, dfs = rows(d, DEP)
    tx = deps[0][dfs.index("transactionHash")]
    del deps[0]
    rs, fs = rows(d, RD)
    rs[:] = [r for r in rs if r[fs.index("transactionHash")] != tx]


def extra_dep(d):
    rs, fs = rows(d, DEP)
    r = list(rs[0])
    r[fs.index("transactionHash")] = NEW_TX
    r[fs.index("data")] = set_words(r[fs.index("data")], {0: lambda v: 0})
    rs.append(r)


def extra_rd(d):
    r, fs = first_mp_rd(d)
    r = list(r)
    r[fs.index("transactionHash")] = NEW_TX
    r[fs.index("data")] = set_words(r[fs.index("data")], {0: lambda v: 0, 1: lambda v: 0, 2: lambda v: 0, 3: lambda v: 0})
    rows(d, RD)[0].append(r)


def bump_vault(label, delta):
    def mutate(d):
        rs, fs = rows(d, label)
        rs[0][fs.index("data")] = set_words(rs[0][fs.index("data")], {0: lambda v: v + delta})
    return mutate


def second_other_emitter(d):
    r, fs = first_other_rd(d)
    r[fs.index("address")] = "0x" + "12" * 20
    d["calls"].append(dict(call(d, f"megapool.exists({OTHER})"), label=f"megapool.exists({'0x' + '12' * 20})"))


def split_other(d):
    r, fs = first_other_rd(d)
    extra = list(r)
    extra[fs.index("logIndex")] = r[fs.index("logIndex")] + 1000
    extra[fs.index("data")] = set_words(r[fs.index("data")], {1: lambda v: 1})
    r[fs.index("data")] = set_words(r[fs.index("data")], {1: lambda v: v - 1})
    rows(d, RD)[0].append(extra)


def setpub(path, value):
    def mutate(d):
        node = d["public"]
        for k in path[:-1]:
            node = node[k]
        node[path[-1]] = value
    return mutate


def shift_block_time(block, seconds):
    def mutate(d):
        d["blocks"][str(block)]["timestamp"] += seconds
    return mutate


FACTORY_BEFORE = "getAddress(contract.addressrocketMegapoolFactory)@24479993"
MANAGER_BEFORE = "getAddress(contract.addressrocketMegapoolManager)@24479993"
FACTORY_AT = "getAddress(contract.addressrocketMegapoolFactory)@24479994"
MANAGER_AT = "getAddress(contract.addressrocketMegapoolManager)@24479994"
SOME_ADDRESS = "0x" + "0" * 24 + "11" * 20
ZERO_WORD = word(0)

MUTATIONS = [
    ("L1", "a megapool loses its flag", SNAP, setnum(f"megapool.exists({MP})", lambda v: 0)),
    ("L1", "the other emitter disappears", SNAP,
     lambda d: query(d, RD)["rows"].__setitem__(slice(None), [r for r in query(d, RD)["rows"] if OTHER not in r])),
    ("L2", "one megapool answers getNodeFee()", SNAP, setcall(f"getNodeFee()@{MP}", ZERO_WORD)),
    ("L2", "30 megapools", SNAP, setnum(f"megapool.exists({MP})", lambda v: 0)),
    ("L3", "one megapool is a minipool", SNAP, setnum(f"getMinipoolExists({MP})", lambda v: 1)),
    ("L4", "minipool count", SNAP, setnum("getMinipoolCount()", lambda v: v - 1)),
    ("L5", "index 0 address", SNAP, setcall("getMinipoolAt(0)", SOME_ADDRESS)),
    ("L5", "index 21158 address", SNAP, setcall("getMinipoolAt(21158)", SOME_ADDRESS)),
    ("L5", "index 42316 address", SNAP, setcall("getMinipoolAt(42316)", SOME_ADDRESS)),
    ("L5", "index 0 values", SNAP, setnum("getNodeFee()@minipool 0", lambda v: v + 10 ** 16)),
    ("L5", "index 21158 values", SNAP, setnum("getNodeDepositBalance()@minipool 21158", lambda v: v * 2)),
    ("L5", "index 42316 values", SNAP, setnum("getNodeFee()@minipool 42316", lambda v: v + 10 ** 16)),
    ("L6", "54 deposits, same total", SNAP, split_dep),
    ("L6", "total off by one wei", SNAP, bump_dep(1)),
    ("L7", "control minipool loses a log", SNAP, lambda d: query(d, "rETH EtherDeposited from minipool 42316")["rows"].pop()),
    ("L8", "54 distributions, same total", SNAP, split_rd),
    ("L8", "both sides one wei higher", SNAP, chain(bump_dep(1), bump_rd(2, 1))),
    ("L8", "residual against the deposits", SNAP, bump_dep(1)),
    ("L9", "a matched pair removed", SNAP, drop_pair),
    ("L9", "an unmatched deposit", SNAP, extra_dep),
    ("L9", "an unmatched distribution", SNAP, extra_rd),
    ("L10", "voter share", SNAP, setnum("getVoterShare()", lambda v: v + 1)),
    ("L10", "node share", SNAP, setnum("getNodeShare()", lambda v: v + 1)),
    ("L10", "protocol DAO share", SNAP, setnum("getProtocolDAOShare()", lambda v: 1)),
    ("L10", "current voter share", SNAP, setnum("getCurrentVoterShare()", lambda v: v + 1)),
    ("L11", "voter amount", SNAP, bump_rd(1, 1)),
    ("L11", "node amount", SNAP, bump_rd(0, 1)),
    ("L11", "protocol DAO amount", SNAP, bump_rd(3, 1)),
    ("L12", "vault deposits differ from the voter share", SNAP,
     chain(bump_vault(VAULT_IN, 1), setnum("rocketVault.balanceOf(rocketRewardsPool)", lambda v: v + 1),
           setnum("getPendingVoterShare()", lambda v: v + 1))),
    ("L12", "withdrawals", SNAP,
     chain(bump_vault(VAULT_OUT, 1), setnum("rocketVault.balanceOf(rocketRewardsPool)", lambda v: v - 1),
           setnum("getPendingVoterShare()", lambda v: v - 1))),
    ("L12", "residual", SNAP, chain(setnum("rocketVault.balanceOf(rocketRewardsPool)", lambda v: v + 1),
                                    setnum("getPendingVoterShare()", lambda v: v + 1))),
    ("L12", "pending voter share", SNAP, setnum("getPendingVoterShare()", lambda v: v + 1)),
    ("L13", "a second unrelated emitter", SNAP, second_other_emitter),
    ("L13", "six unrelated logs, same sum", SNAP, split_other),
    ("L13", "unrelated sum, unfiltered total kept", SNAP, chain(bump_rd(1, 10 ** 9, first_other_rd), bump_rd(1, -10 ** 9))),
    ("L13", "unfiltered total", SNAP, bump_rd(1, 1)),
    ("L14", "factory already set before", SNAP, setcall(FACTORY_BEFORE, SOME_ADDRESS)),
    ("L14", "manager already set before", SNAP, setcall(MANAGER_BEFORE, SOME_ADDRESS)),
    ("L14", "factory unset at the block", SNAP, setcall(FACTORY_AT, ZERO_WORD)),
    ("L14", "manager unset at the block", SNAP, setcall(MANAGER_AT, ZERO_WORD)),
    ("L14", "block date", SNAP, shift_block_time(24479994, 1)),
    ("A1", "ratio", SNAP, bump_rd(1, 10 ** 15)),
    ("A2", "borrowed share", SNAP, bump_rd(0, 10 ** 18)),
    ("A3", "overstatement", SNAP, bump_rd(1, 10 ** 17)),
    ("P1", "no getNodeDepositBalance", SNAP, setpub(["adapter_before_fix", "calls_getNodeDepositBalance"], False)),
    ("P1", "no getNodeFee", SNAP, setpub(["adapter_before_fix", "calls_getNodeFee"], False)),
    ("P1", "no permitFailure", SNAP, setpub(["adapter_before_fix", "permit_failure"], False)),
    ("P1", "no skip branch", SNAP, setpub(["adapter_before_fix", "skips_when_either_is_null"], False)),
    ("P1", "megapool path already there", SNAP, setpub(["adapter_before_fix", "reads_megapool_exists_flag"], True)),
    ("P2", "no subtraction", SNAP, setpub(["adapter_before_fix", "subtracts_24_and_16_eth"], False)),
    ("P3", "#2765 date", SNAP, setpub(["pr_2765", "merged_at"], "2025-04-04T00:00:00Z")),
    ("P3", "#2765 modified, not added", SNAP, setpub(["pr_2765", "adapter_status"], "modified")),
    ("P3", "#2765 without getNodeFee", SNAP, setpub(["pr_2765", "adds_getNodeFee"], False)),
    ("P3", "#3519 date", SNAP, setpub(["pr_3519", "merged_at"], "2025-06-23T00:00:00Z")),
    ("P3", "#3519 without zero revenue", SNAP, setpub(["pr_3519", "adds_zero_revenue"], False)),
    ("O1", "not merged", SNAP, setpub(["pr_8833", "merged_at"], None)),
    ("O1", "does not close #8812", SNAP, setpub(["pr_8833", "body_closes_8812"], False)),
    ("O1", "does not cite the figure", SNAP, setpub(["pr_8833", "body_cites_78_23_eth"], False)),
    ("O2", "no flag after the fix", SNAP, setpub(["adapter_after_fix", "reads_megapool_exists_flag"], False)),
    ("O2", "not scoped to megapools", SNAP, setpub(["adapter_after_fix", "reads_rewards_distributed_of_megapools"], False)),
    ("O3", "open", SNAP, setpub(["issue_8812", "state"], "open")),
    ("O3", "not planned", SNAP, setpub(["issue_8812", "state_reason"], "not_planned")),
    ("O3", "closing date", SNAP, setpub(["issue_8812", "closed_at"], "2026-08-19T00:00:00Z")),
]

CONDITIONS = {"L1": 2, "L2": 2, "L5": 6, "L6": 2, "L8": 3, "L9": 3, "L10": 4, "L11": 3, "L12": 4, "L13": 4, "L14": 5,
              "P1": 5, "P3": 5, "O1": 3, "O2": 2, "O3": 3}


class Base(CaseCopy):
    CASE = CASE
    MODULE = reproduce


class TestPristine(Base):
    def test_exit_zero_and_no_fail(self):
        self.assertEqual(silent(reproduce.main, [], case=self.case), 0)
        self.assertFalse([r["id"] for r in reproduce.results(self.case) if r["status"] == "FAIL"])

    def test_statuses(self):
        rows = reproduce.results(self.case)
        self.assertEqual({r["id"] for r in rows if r["status"] == "OBSERVED"}, {"O1", "O2", "O3"})
        self.assertEqual({r["status"] for r in rows if not r["id"].startswith("O")}, {"MATCH"})

    def test_runs_without_network(self):
        import socket

        def refuse(*a, **k):
            raise AssertionError("reproduce.py must not open a network connection")

        with mock.patch.object(socket, "socket", refuse), mock.patch.object(socket, "create_connection", refuse):
            self.assertEqual(silent(reproduce.main, [], case=self.case), 0)


class TestEveryCondition(Base):
    def test_coverage(self):
        self.check_coverage(MUTATIONS, CONDITIONS)

    def test_each_mutation_fails_its_row(self):
        self.run_mutations(MUTATIONS)


class TestIntegrity(Base):
    def test_tampered_file_is_rejected(self):
        p = self.case / SNAP
        b = bytearray(p.read_bytes())
        b[200] = ord("7") if b[200] != ord("7") else ord("8")
        p.write_bytes(bytes(b))
        self.assertEqual(silent(reproduce.main, [], case=self.case), 1)

    def test_missing_file_is_rejected(self):
        (self.case / SNAP).unlink()
        with self.assertRaisesRegex(reproduce.DataError, "missing"):
            reproduce.results(self.case)

    def test_unlisted_file_is_rejected(self):
        (self.case / "data/extra.json").write_text("{}")
        with self.assertRaisesRegex(reproduce.DataError, "not in MANIFEST"):
            reproduce.results(self.case)

    def test_duplicate_label_is_rejected(self):
        def dup(d):
            d["calls"].append(dict(d["calls"][0]))
        self.edit(self.case, SNAP, dup)
        with self.assertRaisesRegex(reproduce.DataError, "two calls"):
            reproduce.results(self.case)


class TestLiveCheck(Base):
    """live_verify.py --check against stand-ins for the archive node and GitHub."""

    def run_check(self, fresh=None, public=None, argv=("--check",)):
        snap = json.loads((self.case / SNAP).read_text())
        hist = fresh or {k: snap[k] for k in ("blocks", "calls", "logs")}

        def public_state():
            if isinstance(public, Exception):
                raise public
            return public or snap["public"]

        with mock.patch.object(live_verify, "SNAPSHOT", self.case / SNAP), \
                mock.patch.object(live_verify, "archive_checks", lambda rpc: snap["archive_checks"]), \
                mock.patch.object(live_verify, "read_chain", lambda r: hist), \
                mock.patch.object(live_verify, "public_state", public_state):
            return silent(live_verify.main, list(argv), rpc=object(), logs=object())

    def changed(self):
        snap = json.loads((self.case / SNAP).read_text())
        hist = {k: snap[k] for k in ("blocks", "calls", "logs")}
        hist["calls"][0]["result"] = word(1)
        return hist

    def test_identical_is_0(self):
        self.assertEqual(self.run_check(), 0)

    def test_historical_difference_is_1(self):
        self.assertEqual(self.run_check(fresh=self.changed()), 1)

    def test_public_change_is_3(self):
        public = json.loads((self.case / SNAP).read_text())["public"]
        public["issue_8812"]["state"] = "open"
        self.assertEqual(self.run_check(public=public), 3)

    def test_public_failure_still_checks_history(self):
        self.assertEqual(self.run_check(public=RuntimeError("rate limited")), 4)
        self.assertEqual(self.run_check(fresh=self.changed(), public=RuntimeError("rate limited")), 1)

    def test_write_refuses_partial_snapshot(self):
        before = (self.case / SNAP).read_bytes()
        self.assertEqual(self.run_check(public=RuntimeError("rate limited"), argv=("--write",)), 4)
        self.assertEqual((self.case / SNAP).read_bytes(), before)

    def test_no_endpoint_is_2(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(silent(live_verify.main, ["--check"]), 2)

    def test_endpoint_without_history_is_2(self):
        with mock.patch.object(live_verify, "archive_checks", lambda rpc: {"pass": False}):
            self.assertEqual(silent(live_verify.main, ["--check"], rpc=object(), logs=object()), 2)


class TestAdapterFacts(unittest.TestCase):
    PRE = b"""
      const nodeBalances = await options.api.multiCall({ abi: 'uint256:getNodeDepositBalance', permitFailure: true })
      const nodeFees = await options.api.multiCall({ abi: 'uint256:getNodeFee', permitFailure: true })
      if (nodeBalance === null || nodeFeeRate === null) {
        continue
      }
      if (event.etherAmount >= 24e18) {} else if (event.etherAmount >= 16e18) {}
    """

    def test_reads_the_skip_branch(self):
        f = live_verify.adapter_facts(self.PRE)
        self.assertTrue(f["skips_when_either_is_null"] and f["permit_failure"] and f["subtracts_24_and_16_eth"])
        self.assertFalse(f["reads_megapool_exists_flag"] or f["reads_rewards_distributed_of_megapools"])

    def test_skip_branch_must_continue(self):
        f = live_verify.adapter_facts(self.PRE.replace(b"continue", b"return"))
        self.assertFalse(f["skips_when_either_is_null"])

    def test_megapool_path(self):
        post = self.PRE + b"megapool.exists ... options.getLogs({ targets: megapoolAddresses, eventAbi: 'event RewardsDistributed(...)' })"
        f = live_verify.adapter_facts(post)
        self.assertTrue(f["reads_megapool_exists_flag"] and f["reads_rewards_distributed_of_megapools"])


if __name__ == "__main__":
    unittest.main()
