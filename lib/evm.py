"""Shared helpers for the Ethereum cases: Keccak-256, a few ABI encoders, and log/call readers.

Standard library only. Endpoints and keys are read from the environment by the caller and are
never written to disk; nothing here prints them.
"""
import http.client
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

# Keccak-256 as Ethereum uses it (the original Keccak padding, not FIPS 202 SHA3-256).
_RC = [0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
       0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
       0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
       0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
       0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
       0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008]
_ROT = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61], [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]]
_MASK = (1 << 64) - 1


def _rol(x, n):
    return ((x << n) | (x >> (64 - n))) & _MASK if n else x


def _permute(a):
    for rc in _RC:
        c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        a = [[a[x][y] ^ d[x] for y in range(5)] for x in range(5)]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rol(a[x][y], _ROT[x][y])
        a = [[b[x][y] ^ (~b[(x + 1) % 5][y] & b[(x + 2) % 5][y]) for y in range(5)] for x in range(5)]
        a[0][0] ^= rc
    return a


def keccak256(data):
    rate = 136
    msg = bytearray(data) + b"\x01"
    msg += b"\x00" * (-len(msg) % rate)
    msg[-1] |= 0x80
    a = [[0] * 5 for _ in range(5)]
    for off in range(0, len(msg), rate):
        for i in range(rate // 8):
            a[i % 5][i // 5] ^= int.from_bytes(msg[off + 8 * i:off + 8 * i + 8], "little")
        a = _permute(a)
    return b"".join(a[i % 5][i // 5].to_bytes(8, "little") for i in range(4))


def hex32(b):
    return "0x" + b.hex()


def selector(signature):
    return "0x" + keccak256(signature.encode()).hex()[:8]


def topic(signature):
    return hex32(keccak256(signature.encode()))


def word_address(addr):
    return "0x" + "0" * 24 + addr.lower()[2:]


def enc_uint(n):
    return format(n, "064x")


def enc_string(s):
    """ABI encoding of a single dynamic `string` argument (offset, length, padded bytes)."""
    raw = s.encode()
    return enc_uint(32) + enc_uint(len(raw)) + raw.hex() + "0" * (-len(raw.hex()) % 64)


def calldata(signature, *encoded_args):
    return selector(signature) + "".join(encoded_args)


def words(data):
    data = data[2:] if data.startswith("0x") else data
    return [int(data[i:i + 64], 16) for i in range(0, len(data), 64)]


def address_of(word_hex):
    return "0x" + word_hex[-40:].lower()


def storage_key(prefix, tail=b""):
    """keccak256(abi.encodePacked(prefix, tail)), the key layout RocketStorage uses."""
    return hex32(keccak256(prefix.encode() + tail))


def normalize_log(x):
    """Keep only on-chain fields, in one shape whichever reader returned the log."""
    as_int = lambda v: (int(v, 16) if v not in ("0x", "") else 0) if isinstance(v, str) else int(v)  # Etherscan writes index 0 as "0x"
    return {"blockNumber": as_int(x["blockNumber"]), "logIndex": as_int(x["logIndex"]),
            "transactionHash": x["transactionHash"].lower(), "address": x["address"].lower(),
            "topics": [t.lower() for t in x["topics"]], "data": x["data"].lower()}


def sort_logs(logs):
    return sorted(logs, key=lambda l: (l["blockNumber"], l["logIndex"]))


def _implied(address, topics):
    """Fields a log query fixes, so they need not be stored with every row."""
    out = {}
    if address:
        out["address"] = address.lower()
    if all(t is not None for t in topics):
        out["topics"] = [t.lower() for t in topics]
    return out


def matches_filter(log, address, topics):
    """True if a log satisfies the query it came from: the address, and every topic position the query fixes."""
    if address and log["address"] != address.lower():
        return False
    if all(t is not None for t in topics) and len(log["topics"]) != len(topics):
        return False
    return len(log["topics"]) >= len(topics) and all(t is None or log["topics"][i] == t.lower() for i, t in enumerate(topics))


def pack(label, address, topics, lo, hi, logs):
    """A log query as stored in a snapshot: the filter, then one row per log without the fields the filter fixes."""
    implied = _implied(address, topics)
    fields = ["blockNumber", "logIndex", "transactionHash"] + [f for f in ("address", "topics") if f not in implied] + ["data"]
    for l in logs:
        if not matches_filter(l, address, topics) or not lo <= l["blockNumber"] <= hi:
            raise ReaderError(f"{label}: a log does not match its own filter")
    return {"label": label, "address": address and address.lower(), "topics": [t and t.lower() for t in topics],
            "from": lo, "to": hi, "fields": fields, "rows": [[l[f] for f in fields] for l in sort_logs(logs)]}


def unpack(query):
    """The logs of a stored query, in normalize_log() shape."""
    implied = _implied(query["address"], query["topics"])
    return [dict(zip(query["fields"], row), **implied) for row in query["rows"]]


class ReaderError(Exception):
    """A read that did not give an answer (as opposed to a call that reverted)."""


TRANSPORT_ERRORS = (urllib.error.URLError, http.client.HTTPException, socket.timeout, TimeoutError, ConnectionError,
                    json.JSONDecodeError)
THROTTLE_WORDS = ("rate limit", "rate-limit", "ratelimit", "rate exceeded", "too many requests", "capacity", "timeout", "timed out")


def _throttled(error):
    """A JSON-RPC error that means "slow down", as opposed to an answer or a refused request."""
    msg = str(error.get("message", "")).lower()
    return error.get("code") == 429 or any(w in msg for w in THROTTLE_WORDS)


def _post(url, body, timeout=60):
    req = urllib.request.Request(url, json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


class Rpc:
    """JSON-RPC reader. Retries transport errors and rate limits; a revert is an answer, not an error."""

    def __init__(self, url, tries=5, pause=1.0):
        self.url, self.tries, self.pause = url, tries, pause

    def raw(self, method, params):
        last = None
        for i in range(self.tries):
            try:
                return _post(self.url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            except TRANSPORT_ERRORS as e:
                last = e
                time.sleep(self.pause * (i + 1))
        raise ReaderError(f"{method}: {type(last).__name__}")

    def request(self, method, params):
        for i in range(self.tries):
            reply = self.raw(method, params)
            if "result" in reply:
                return reply["result"]
            error = reply.get("error") or {}
            if _throttled(error):
                time.sleep(self.pause * (i + 1))
                continue
            raise ReaderError(f"{method}: {str(error.get('message', ''))[:120]}")
        raise ReaderError(f"{method}: rate limited")

    def block(self, number):
        b = self.request("eth_getBlockByNumber", [hex(number), False])
        if not b:
            raise ReaderError(f"block {number} not found")
        return {"hash": b["hash"].lower(), "timestamp": int(b["timestamp"], 16)}

    def call(self, to, data, block):
        """{"result": hex} if the call returned, {"reverted": True} if it reverted."""
        for i in range(self.tries):
            reply = self.raw("eth_call", [{"to": to, "data": data}, hex(block)])
            if "result" in reply:
                return {"result": reply["result"].lower()}
            error = reply.get("error") or {}
            if "revert" in str(error.get("message", "")).lower():
                return {"reverted": True}
            if _throttled(error):
                time.sleep(self.pause * (i + 1))
                continue
            raise ReaderError(f"eth_call {to}: {str(error.get('message', ''))[:120]}")
        raise ReaderError(f"eth_call {to}: rate limited")

    CAP = 10000  # many endpoints truncate at this many results; treat a full answer as possibly cut

    def logs(self, address, topics, lo, hi):
        """eth_getLogs over [lo, hi], halving the range whenever the endpoint refuses it or returns a full page."""
        flt = {"fromBlock": hex(lo), "toBlock": hex(hi), "topics": topics}
        if address:
            flt["address"] = address
        try:
            rows = self.request("eth_getLogs", [flt])
        except ReaderError:
            rows = None
        if rows is not None and len(rows) < self.CAP:
            return [normalize_log(x) for x in rows]
        if lo == hi:
            raise ReaderError(f"eth_getLogs refused or capped at block {lo}")
        mid = (lo + hi) // 2
        return self.logs(address, topics, lo, mid) + self.logs(address, topics, mid + 1, hi)


class Etherscan:
    """Logs through the Etherscan API v2. Results are capped at 1,000 per request, so a full page
    is split by block range until every page is below the cap."""

    URL = "https://api.etherscan.io/v2/api"

    def __init__(self, key, chain_id=1, pause=0.25, tries=5):
        self.key, self.chain_id, self.pause, self.tries = key, chain_id, pause, tries

    def _get(self, params):
        q = dict(params, chainid=self.chain_id, apikey=self.key)
        req = urllib.request.Request(self.URL + "?" + urllib.parse.urlencode(q), headers={"User-Agent": "Mozilla/5.0"})
        for i in range(self.tries):
            time.sleep(self.pause)
            try:
                r = json.loads(urllib.request.urlopen(req, timeout=60).read())
            except TRANSPORT_ERRORS:
                time.sleep(1 + i)
                continue
            # Only two answers count: a list of logs, or "No records found" with an empty list.
            # Anything else is an error, never an empty result.
            if r.get("status") == "1" and isinstance(r.get("result"), list):
                return r["result"]
            if r.get("status") == "0" and r.get("message") == "No records found" and r.get("result") == []:
                return []
            if "rate limit" in str(r.get("result", "")).lower():
                time.sleep(1 + i)
                continue
            raise ReaderError(f"etherscan: {str(r.get('result'))[:120]}")
        raise ReaderError("etherscan: no answer")

    def logs(self, address, topics, lo, hi):
        params = {"module": "logs", "action": "getLogs", "fromBlock": lo, "toBlock": hi, "page": 1, "offset": 1000}
        if address:
            params["address"] = address
        set_topics = [i for i, t in enumerate(topics) if t is not None]
        for i in set_topics:
            params[f"topic{i}"] = topics[i]
        for a, b in zip(set_topics, set_topics[1:]):
            params[f"topic{a}_{b}_opr"] = "and"
        if len(set_topics) == 3:
            params[f"topic{set_topics[0]}_{set_topics[2]}_opr"] = "and"
        rows = self._get(params)
        if len(rows) >= 1000:
            if lo == hi:
                raise ReaderError(f"more than 1,000 logs in block {lo}")
            mid = (lo + hi) // 2
            return self.logs(address, topics, lo, mid) + self.logs(address, topics, mid + 1, hi)
        return [normalize_log(x) for x in rows]
