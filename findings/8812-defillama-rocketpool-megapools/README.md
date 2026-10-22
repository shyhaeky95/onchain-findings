# Rocket Pool fees: megapool reward deposits skipped

**Report:** [DefiLlama/dimension-adapters#8812](https://github.com/DefiLlama/dimension-adapters/issues/8812), posted 2026-08-17 05:13 UTC, closed 2026-08-18.
**Outcome:** [PR #8833](https://github.com/DefiLlama/dimension-adapters/pull/8833) (merged 2026-08-18) adds a megapool path to `fees/rocketpool.ts`: it reads `RewardsDistributed` from emitters that Rocket Pool's own `megapool.exists` flag confirms, and its description uses the report's 78.23 ETH figure.

## What the report showed

`fees/rocketpool.ts` reads rETH `EtherDeposited` and, for each sender, calls the minipool functions `getNodeDepositBalance()` and `getNodeFee()` with `permitFailure: true`, skipping the deposit when either returns null. Since Saturn 1, node operators can run megapools, which deposit their reward share through the same event but do not implement those functions. At block 25,772,201 both calls revert on all 31 megapools that had emitted `RewardsDistributed`, so their 53 deposits, 78.234366859157413918 ETH up to block 25,772,216, were skipped. The deposits pair one-to-one with the megapools' own `RewardsDistributed` logs, so they are reward distributions, not capital repayments. The figure is a lower bound: megapools that had not yet emitted `RewardsDistributed` are not in the seed set.

## Run

```bash
python3 reproduce.py                                                    # offline: no network, no key
ETH_ARCHIVE_RPC=<archive endpoint> python3 live_verify.py --check         # optional: re-read and compare
```

`reproduce.py` checks the data file against `MANIFEST.json`, recomputes each claim, and prints one row per claim. It exits 1 on any FAIL or changed file. Before any row, it checks that every call in the snapshot is the call its label names (target, calldata, block) and every log query has the filter and block range its label names. `live_verify.py --check` repeats every call and log query at its block and compares with the committed snapshot: exit 1 if a historical read differs, 2 if the endpoint does not serve history or a read failed, 3 if only the public sources have moved, 4 if a public source could not be read (the historical check still runs). Logs go to the same endpoint as `eth_getLogs`, or to the Etherscan API v2 if `ETHERSCAN_API_KEY` is set; `GITHUB_TOKEN`, if set, lifts GitHub's limit of 60 unauthenticated calls an hour. Tests: [`../../tests/test_8812.py`](../../tests/test_8812.py) changes the data for each condition a row checks and checks that the row turns into FAIL.

## How to read a row

Each row puts the report's own wording ("stated") next to what the evidence gives ("computed").

| Status | Meaning |
|---|---|
| MATCH | The evidence supports the statement, for every condition the row lists. |
| RECORDED | The statement exists here only as the report wrote it; nothing here re-derives it. Not counted as a match. |
| OBSERVED | A fact about the sources after the fix (O rows). Not a statement of the report, so not counted as a match. |
| FAIL | Anything else. The row's note names the condition that does not hold. |

| Evidence | Rows | What it rests on |
|---|---|---|
| Re-read at block, 2026-10-22 snapshot | L1–L14 | Calls at blocks 25,772,201, 25,772,216, 24,479,993 and 24,479,994 through an archive node, and log queries from block 0 (or from 24,416,500, as the original run did for `RewardsDistributed`) to 25,772,216 through the Etherscan API v2, read on 2026-10-22 and stored with block hashes. `reproduce.py` reads the stored snapshot, not the chain; `live_verify.py --check` repeats the read. |
| Arithmetic | A1–A4 | Ratios of the rows above, and Keccak-256 of the event signature for the topic0 the report prints. |
| Public sources as of 2026-10-22 (snapshot) | P1–P4, O1–O3 | The adapter at the parent of the fix's merge commit and at the merge commit (stored as a SHA-256 and a few pattern checks, not the file), and the pull requests and issue on GitHub. |
| Recorded (report text) | R1 | Two strings the report quotes from DefiLlama's API. |

Current result for the report's statements: **22 match (14 re-read, 4 arithmetic, 4 public-source), 0 errata, 1 recorded only.** Post-fix observations: 3. Fail: 0.

## Limits

- **A lower bound.** The seed set is the emitters of `RewardsDistributed` (from block 24,416,500 to 25,772,216) that Rocket Pool flags as megapools. Megapools that had not emitted the event are not counted; the registry was not enumerated.
- **Read on 2026-10-22, pinned to the report's blocks.** The original run read calls at its head block (25,772,201) and logs up to 25,772,216; its raw responses are not kept. This case reads the same blocks again; the values agree to the wei.
- **Code facts are pattern checks.** P1, P2, P4 and O2 check that the adapter source contains the patterns and strings described (the two calls with `permitFailure: true`, the skip when either is null, the 24e18/16e18 subtraction, the methodology strings, the megapool flag). Neither the report nor this case runs the adapter.
- **Two strings stand only as the report quoted them (R1).** The report also quotes `UserFees` and `ProtocolRevenue` strings from DefiLlama's API response ([`api.llama.fi/summary/fees/rocket-pool?dataType=dailyFees`](https://api.llama.fi/summary/fees/rocket-pool?dataType=dailyFees)) and says it does not rely on them. This case keeps no DefiLlama data, so it does not re-read them. The strings that come from the adapter itself are checked (P4).
- **The field order of `RewardsDistributed`** is taken from `RocketMegapoolDelegate.sol` (`nodeAmount, voterAmount, rethAmount, protocolDaoAmount, time`). L9 supports it: the time field equals the one in the paired `EtherDeposited` in all 53 pairs.
- **Live checks need an archive endpoint**, and a log reader that accepts wide block ranges (or the Etherscan API). `live_verify.py` first checks that the endpoint serves history (`RocketStorage` has no code at block 1, and the megapool factory address read 100,000 blocks before the activation differs from the one at block 25,772,201) and never writes the endpoint or key to disk.

## Data

| Path | Content |
|---|---|
| `data/live_snapshot.json` | Output of `live_verify.py --write` on 2026-10-22: block headers, 153 calls and 177 logs (on-chain fields only), and the public-source facts |
| `MANIFEST.json` | SHA-256, size and provenance of the data file |

Everything in `data/` is Ethereum chain data or facts about public GitHub pages; no provider's dataset is stored. Logs were read through the Etherscan API v2 and calls through an archive node, and only on-chain fields are kept, so another reader gives the same file. Shared helpers (Keccak-256, ABI encoding, the log readers) are in [`../../lib/evm.py`](../../lib/evm.py).
