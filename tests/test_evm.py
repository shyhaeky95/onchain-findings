"""Tests for lib/evm.py: Keccak-256, ABI helpers, stored log queries, and the two log readers."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import evm  # noqa: E402


class TestKeccak(unittest.TestCase):
    # Published Keccak-256 values (Ethereum padding).
    VECTORS = {
        b"": "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
        b"abc": "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45",
        b"Transfer(address,address,uint256)": "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
    }

    def test_vectors(self):
        for msg, want in self.VECTORS.items():
            with self.subTest(msg=msg):
                self.assertEqual(evm.keccak256(msg).hex(), want)

    def test_block_boundaries(self):
        # 135, 136 and 137 bytes straddle the 136-byte rate: one-byte padding, a full extra block, and the normal case.
        outs = {n: evm.keccak256(b"a" * n).hex() for n in (135, 136, 137, 272)}
        self.assertEqual(len(set(outs.values())), 4)
        self.assertEqual(outs[135], "34367dc248bbd832f4e3e69dfaac2f92638bd0bbd18f2912ba4ef454919cf446")

    def test_selectors_and_topics(self):
        self.assertEqual(evm.selector("balanceOf(address)"), "0x70a08231")
        self.assertEqual(evm.selector("totalSupply()"), "0x18160ddd")
        self.assertEqual(evm.topic("Transfer(address,address,uint256)"), "0x" + self.VECTORS[b"Transfer(address,address,uint256)"])


class TestAbi(unittest.TestCase):
    def test_enc_string(self):
        enc = evm.enc_string("rocketRewardsPool")
        self.assertEqual(enc[:64], format(32, "064x"))
        self.assertEqual(enc[64:128], format(17, "064x"))
        self.assertEqual(bytes.fromhex(enc[128:128 + 34]), b"rocketRewardsPool")
        self.assertEqual(len(enc) % 64, 0)
        self.assertEqual(set(enc[128 + 34:]), {"0"})

    def test_word_address_and_back(self):
        a = "0xAE78736Cd615f374D3085123A210448E74Fc6393"
        self.assertEqual(evm.address_of(evm.word_address(a)), a.lower())

    def test_storage_key_is_packed(self):
        self.assertEqual(evm.storage_key("contract.address", b"rocketVault"),
                         evm.hex32(evm.keccak256(b"contract.addressrocketVault")))

    def test_words(self):
        self.assertEqual(evm.words("0x" + format(1, "064x") + format(2, "064x")), [1, 2])


class TestPack(unittest.TestCase):
    LOGS = [{"blockNumber": 2, "logIndex": 1, "transactionHash": "0xbb", "address": "0xaa", "topics": ["0xt0", "0xt1"], "data": "0x02"},
            {"blockNumber": 1, "logIndex": 5, "transactionHash": "0xcc", "address": "0xaa", "topics": ["0xt0", "0xt1"], "data": "0x01"}]

    def test_round_trip_with_fixed_fields(self):
        q = evm.pack("q", "0xAA", ["0xT0", "0xT1"], 0, 9, self.LOGS)
        self.assertEqual(q["fields"], ["blockNumber", "logIndex", "transactionHash", "data"])
        self.assertEqual(evm.unpack(q), evm.sort_logs(self.LOGS))

    def test_round_trip_with_free_topic(self):
        q = evm.pack("q", None, ["0xt0", None], 0, 9, self.LOGS)
        self.assertIn("address", q["fields"])
        self.assertIn("topics", q["fields"])
        self.assertEqual(evm.unpack(q), evm.sort_logs(self.LOGS))

    def test_log_outside_its_filter_is_refused(self):
        with self.assertRaises(evm.ReaderError):
            evm.pack("q", "0xaa", ["0xt0", "0xother"], 0, 9, self.LOGS)

    def test_filter_shorter_than_the_event_is_refused(self):
        with self.assertRaises(evm.ReaderError):
            evm.pack("q", "0xaa", ["0xt0"], 0, 9, self.LOGS)

    def test_fixed_position_is_checked_when_another_is_free(self):
        with self.assertRaises(evm.ReaderError):
            evm.pack("q", "0xaa", [None, "0xother"], 0, 9, self.LOGS)

    def test_log_outside_the_block_range_is_refused(self):
        with self.assertRaises(evm.ReaderError):
            evm.pack("q", "0xaa", ["0xt0", "0xt1"], 0, 1, self.LOGS)

    def test_etherscan_writes_index_zero_as_0x(self):
        log = evm.normalize_log({"blockNumber": "0x10", "logIndex": "0x", "transactionHash": "0xAB", "address": "0xAA",
                                 "topics": ["0xT0"], "data": "0x"})
        self.assertEqual((log["blockNumber"], log["logIndex"]), (16, 0))


def raw_log(block, index=0):
    return {"blockNumber": hex(block), "logIndex": hex(index), "transactionHash": "0x" + format(block, "064x"),
            "address": "0xAA", "topics": ["0xT0"], "data": "0x00"}


class TestEtherscanSplitting(unittest.TestCase):
    def test_full_page_is_split_by_block_range(self):
        # 1,500 logs at blocks 0..1499: a single request returns the 1,000-row cap, so the range must be halved.
        def fake(params):
            lo, hi = params["fromBlock"], params["toBlock"]
            return [raw_log(b) for b in range(lo, min(hi, 1499) + 1)][:1000]
        es = evm.Etherscan("k", pause=0)
        with mock.patch.object(es, "_get", side_effect=fake):
            logs = es.logs("0xaa", ["0xt0"], 0, 1499)
        self.assertEqual(len(logs), 1500)
        self.assertEqual(len({l["blockNumber"] for l in logs}), 1500)

    def test_more_than_a_page_in_one_block_is_an_error(self):
        es = evm.Etherscan("k", pause=0)
        with mock.patch.object(es, "_get", return_value=[raw_log(7, i) for i in range(1000)]):
            with self.assertRaises(evm.ReaderError):
                es.logs("0xaa", ["0xt0"], 7, 7)

    def test_topic_operators(self):
        seen = {}
        es = evm.Etherscan("k", pause=0)
        with mock.patch.object(es, "_get", side_effect=lambda p: seen.update(p) or []):
            es.logs("0xaa", ["0xt0", None, "0xt2"], 0, 1)
        self.assertEqual((seen["topic0"], seen["topic2"], seen["topic0_2_opr"]), ("0xt0", "0xt2", "and"))
        self.assertNotIn("topic1", seen)


class TestEtherscanAnswers(unittest.TestCase):
    """Etherscan._get must never turn a malformed or failed answer into an empty list of logs."""

    def answer(self, payload):
        es = evm.Etherscan("k", pause=0, tries=1)
        body = mock.Mock()
        body.read.return_value = __import__("json").dumps(payload).encode()
        with mock.patch("urllib.request.urlopen", return_value=body):
            return es._get({"module": "logs"})

    def test_logs(self):
        self.assertEqual(self.answer({"status": "1", "message": "OK", "result": [{"a": 1}]}), [{"a": 1}])

    def test_no_records(self):
        self.assertEqual(self.answer({"status": "0", "message": "No records found", "result": []}), [])

    def test_ok_with_a_non_list_result_is_an_error(self):
        with self.assertRaises(evm.ReaderError):
            self.answer({"status": "1", "message": "OK", "result": "this-is-not-a-list"})

    def test_no_records_with_a_non_empty_result_is_an_error(self):
        with self.assertRaises(evm.ReaderError):
            self.answer({"status": "0", "message": "No records found", "result": "x"})

    def test_other_failures_are_errors(self):
        for payload in ({"status": "0", "message": "NOTOK", "result": "Invalid API Key"}, {"message": "OK"}, {}):
            with self.subTest(payload=payload), self.assertRaises(evm.ReaderError):
                self.answer(payload)


class TestRpc(unittest.TestCase):
    def test_revert_is_an_answer(self):
        rpc = evm.Rpc("http://x", pause=0)
        with mock.patch.object(rpc, "raw", return_value={"error": {"code": 3, "message": "execution reverted"}}):
            self.assertEqual(rpc.call("0xaa", "0x", 1), {"reverted": True})

    def test_rate_limit_is_retried_not_read_as_revert(self):
        rpc = evm.Rpc("http://x", pause=0)
        replies = [{"error": {"message": "rate limit exceeded"}}, {"result": "0x01"}]
        with mock.patch.object(rpc, "raw", side_effect=replies):
            self.assertEqual(rpc.call("0xaa", "0x", 1), {"result": "0x01"})

    def test_other_errors_raise(self):
        rpc = evm.Rpc("http://x", pause=0)
        with mock.patch.object(rpc, "raw", return_value={"error": {"message": "missing trie node"}}):
            with self.assertRaises(evm.ReaderError):
                rpc.call("0xaa", "0x", 1)

    def test_a_range_error_is_not_read_as_throttling(self):
        self.assertFalse(evm._throttled({"code": -32600, "message": "query exceeds max block range limit"}))
        self.assertTrue(evm._throttled({"code": 429, "message": "Too many requests"}))
        self.assertTrue(evm._throttled({"code": -32005, "message": "slow down"}))

    def test_socket_timeout_is_retried(self):
        import socket
        rpc = evm.Rpc("http://x", pause=0)
        with mock.patch.object(evm, "_post", side_effect=[socket.timeout("t"), {"result": "0x1"}]):
            self.assertEqual(rpc.request("eth_blockNumber", []), "0x1")

    def test_full_page_from_rpc_is_split(self):
        rpc = evm.Rpc("http://x", pause=0)
        rpc.CAP = 10

        def fake(method, params):
            lo, hi = int(params[0]["fromBlock"], 16), int(params[0]["toBlock"], 16)
            return [raw_log(b) for b in range(lo, hi + 1)][:rpc.CAP]
        with mock.patch.object(rpc, "request", side_effect=fake):
            logs = rpc.logs(None, ["0xt0"], 0, 99)
        self.assertEqual([l["blockNumber"] for l in logs], list(range(100)))

    def test_refused_range_is_halved(self):
        rpc = evm.Rpc("http://x", pause=0)

        def fake(method, params):
            lo, hi = int(params[0]["fromBlock"], 16), int(params[0]["toBlock"], 16)
            if hi - lo > 10:
                raise evm.ReaderError("range too large")
            return [raw_log(b) for b in range(lo, hi + 1)]
        with mock.patch.object(rpc, "request", side_effect=fake):
            logs = rpc.logs(None, ["0xt0"], 0, 99)
        self.assertEqual([l["blockNumber"] for l in logs], list(range(100)))


if __name__ == "__main__":
    unittest.main()
