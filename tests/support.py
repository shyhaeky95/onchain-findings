"""Helpers shared by the case tests: temporary case copies, edits that keep the manifest consistent,
and accessors for the packed log queries in a snapshot."""
import contextlib
import importlib.util
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def silent(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*args, **kwargs)


def query(d, label):
    return next(q for q in d["logs"] if q["label"] == label)


def call(d, label):
    return next(c for c in d["calls"] if c["label"] == label)


def word(value):
    return "0x" + format(value, "064x")


def data_words(hexdata):
    h = hexdata[2:]
    return [int(h[i:i + 64], 16) for i in range(0, len(h), 64)]


def set_words(hexdata, changes):
    """changes: {index: function old -> new} on the 32-byte words of a log's data."""
    w = data_words(hexdata)
    for i, f in changes.items():
        w[i] = f(w[i])
    return "0x" + "".join(format(x, "064x") for x in w)


class CaseCopy(unittest.TestCase):
    CASE = None
    MODULE = None  # the case's reproduce module

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.case = self.copy("case")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def copy(self, name):
        dest = self.tmp / name
        shutil.copytree(self.CASE, dest)
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
                f["sha256"], f["bytes"] = self.MODULE.sha256(p), p.stat().st_size
        (case / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))

    @staticmethod
    def row(rows, cid):
        return next(r for r in rows if r["id"] == cid)

    def run_mutations(self, mutations, errata=()):
        for i, (cid, label, rel, mutate) in enumerate(mutations):
            with self.subTest(claim=cid, mutation=label):
                case = self.copy(f"m{i}")
                self.edit(case, rel, mutate)
                row = self.row(self.MODULE.results(case), cid)
                self.assertEqual(row["status"], "FAIL")
                if cid in errata and label.startswith("statement:"):
                    self.assertIn("erratum is wrong", row["note"])
                elif cid in errata and label.startswith("correction:"):
                    self.assertIn("correction no longer holds", row["note"])
                self.assertEqual(silent(self.MODULE.main, [], case=case), 1)

    def check_coverage(self, mutations, conditions, errata=(), not_data=()):
        ids = {r["id"] for r in self.MODULE.results(self.case)} - set(not_data)
        self.assertEqual({m[0] for m in mutations}, ids)
        for cid in ids:
            with self.subTest(claim=cid):
                self.assertGreaterEqual(sum(m[0] == cid for m in mutations), conditions.get(cid, 1))
        for cid in errata:
            labels = [m[1] for m in mutations if m[0] == cid]
            self.assertTrue(any(l.startswith("statement:") for l in labels))
            self.assertTrue(any(l.startswith("correction:") for l in labels))
