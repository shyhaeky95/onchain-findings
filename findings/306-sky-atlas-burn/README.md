# SKY buybacks and the Atlas "are burned" clause

**Report:** [sky-ecosystem/next-gen-atlas#306](https://github.com/sky-ecosystem/next-gen-atlas/issues/306), posted 2026-08-18 12:00 UTC, closed the same day.
**Outcome:** answered, not changed. The maintainer replied that *"the tokens will be burned in a future executive vote - no change to the Atlas is required for this"* (O1). Two burns followed, both sent from `MCD_PAUSE_PROXY`, the contract executive spells act through: 2,860,943.76 SKY on 2026-09-13 and 7,372,288 SKY on 2026-10-12. Each equals, within 1 SKY, 10/55 of the SKY the Smart Burn Engine bought in the preceding period (from the cast to 31 August; September, by UTC), and no other contiguous run of kicks comes within 1 SKY (O3, O4). The first burn's window includes the 16 kicks of window A, whose 10/55 share is the 177,481.93 SKY the report named. These burns strongly support the maintainer's explanation and meet the falsifier the report proposed, at the level of aggregate amounts: nothing here traces the SKY of individual kicks to the burn transactions or reads the spells' code. They do not change the report's measurement (in the 0.75 days after the cast, `totalSupply` did not fall), and the forum note does say "non-issuance". The quoted passages, clause names and `[Core]` type are the same as at the report (O5).

## What the report showed

Two `[Core]` clauses of the Sky Atlas say SKY bought back by the Smart Burn Engine is burned (`A.2.3.1.2.4`: *"… and the SKY tokens acquired through these buybacks are burned"*), while the implementation note published with the configuration says the 10% allocation is implemented through non-issuance, with no explicit burn. In the 0.75 days after the executive spell that brought in the 10% allocation (blocks 25,775,271 to 25,780,667), the engine bought 976,150.62 SKY in 16 kicks and `SKY.totalSupply()` did not change by one wei. Over 395 days (from block 22,950,000) the only supply-reducing events were three transfers totalling 1,002 wei. The verified `Sky.sol` changes `totalSupply` only in `mint` and `burn`, each with its `Transfer` from or to `address(0)`, so the sweep of those transfers is a complete record of supply changes.

## Run

```bash
python3 reproduce.py                                                    # offline: no network, no key
ETH_ARCHIVE_RPC=<archive endpoint> python3 live_verify.py --check         # optional: re-read and compare
```

`reproduce.py` checks the data files against `MANIFEST.json`, recomputes each claim, and prints one row per claim. It exits 1 on any FAIL or changed file. Before any row, it checks that every call is the call its label names (target, calldata, block), that every log query has the filter and block range its label names, that every stored log satisfies its filter, and that the contract addresses are the ones the Sky chainlog returned. `live_verify.py --check` repeats every call and log query at its block (the post-report rows use the head block pinned when the snapshot was written) and compares: exit 1 if a historical read differs, 2 if the endpoint does not serve history or a read failed, 3 if only the public sources have moved, 4 if a public source could not be read (the historical check still runs). Logs go to the same endpoint as `eth_getLogs`, or to the Etherscan API v2 if `ETHERSCAN_API_KEY` is set; `GITHUB_TOKEN`, if set, lifts GitHub's limit of 60 unauthenticated calls an hour. Tests: [`../../tests/test_306.py`](../../tests/test_306.py) changes the data for each condition a row checks, in both directions for the erratum, and checks that the row turns into FAIL.

## How to read a row

Each row puts the report's own wording ("stated") next to what the evidence gives ("computed").

| Status | Meaning |
|---|---|
| MATCH | The evidence supports the statement, for every condition the row lists. |
| ERRATUM | The statement is wrong, and the evidence equals the correction documented below, including every fact the correction relies on. If any of those stops holding, or the statement becomes true, the row is FAIL. |
| RECORDED | The statement exists here only as the report wrote it; nothing here re-derives it. Not counted as a match. |
| OBSERVED | A fact about the sources after the report (O rows). Not a statement of the report, so not counted as a match. |
| FAIL | Anything else. The row's note names the condition that does not hold. |

| Evidence | Rows | What it rests on |
|---|---|---|
| Re-read at block, 2026-10-22 snapshot | L1–L12, O2–O4 | `totalSupply`, balances, `MCD_SPLIT.burn()`, the chainlog and `MCD_FLAP.pair()` at the report's blocks through an archive node, and every `Transfer` the report counts through the Etherscan API v2, read on 2026-10-22 and stored with block hashes. Contract addresses come from the Sky chainlog at block 25,780,667, and the flapper's pair from `MCD_FLAP.pair()`. `reproduce.py` reads the stored files, not the chain; `live_verify.py --check` repeats the read. |
| Arithmetic | A1 | 10/55 of the SKY bought in window A. |
| Public sources as of 2026-10-22 (snapshot) | P1–P5, O1, O5 | `Sky.sol` from Sourcify for the deployed address; the Atlas repository at the last commit before 2026-07-16, at the last commit before the report and at the current head; the forum post with the implementation note; the issue and its reply on GitHub. Stored as facts and checksums, not as copies. |
| Recorded (report text) | R1 | The report's positive-control counts for its Atlas search. |

Current result for the report's statements: **17 match (11 re-read, 1 arithmetic, 5 public-source), 1 erratum, 1 recorded only.** Post-report observations: 5. Fail: 0.

## Errata

- **E1 (L10).** "All three supply-reducing events occurred on 2026-01-16 within 18 minutes." The first is at 08:25:23 UTC and the last at 08:43:59 UTC: 18 minutes 36 seconds. Nothing else in the report depends on it.

## Limits

- **Window A is 0.75 days.** It supports statements about the period right after the configuration took effect, as the report says, and nothing longer. The later rows (O2–O4) are what shows the longer-run behaviour.
- **The monthly windows in O3 and O4 are this case's reading, and the only ones that fit.** O3 compares the 2026-09-13 burn with the SKY bought from the cast block to the last block of 31 August (UTC); O4 compares the 2026-10-12 burn with the SKY bought from 1 to 30 September. The differences are −0.0012 SKY and +0.42 SKY (the September burn is rounded to the cent, the October one to a whole SKY). `reproduce.py` tries every contiguous run of the 1,157 kicks from the cast to 30 September against each burn; only these two runs come within 1 SKY. A single kick's 10/55 share is thousands of SKY, so moving a window by one kick misses by that much. The spells' own code was not read here; the match is between amounts, and "spells" is inferred from the sender.
- **The report's positive-control counts for its Atlas search are not reproduced (R1).** It gives `Smart Burn Engine` 30 and `USDS` 1,182 on "the same snapshot". That snapshot was assembled privately from the Atlas repository and is not kept, and its counts depend on how the clauses were joined; the repository itself went from one file per clause (14,489 files at `2683f34`, 2026-07-13) to 18 files (at `0580b70`, the last commit before the report). On the files at `0580b70` the same phrases occur 55 and 1,562 times. The zero matches for the four implementation-note phrases (P3) do not depend on how the files are joined.
- **The Atlas "the day the report was posted"** is the last commit on `main` before 2026-08-18 12:00:21 UTC (`0580b70`, committed 2026-08-17 20:53 UTC).
- **`Sky.sol` is the verified source on Sourcify**, a single file, with both the runtime and the creation bytecode reported as a match (P1). P1 checks the lines that assign `totalSupply` and the `emit` that follows each; it does not compile the source.
- **The long ranges reuse the flapper and pair read in August 2026.** L11 checks that the chainlog's `MCD_FLAP` and its `pair()` were the same at blocks 22,950,000 and 25,578,353 as at the cast. That does not rule out a change and a change back in between; the report's own count used the same flapper and pair.
- **Live checks need an archive endpoint**, and a log reader that accepts wide block ranges (or the Etherscan API). `live_verify.py` first checks that the endpoint serves history (the chainlog has no code at block 1, and `MCD_SPLIT` is not registered at block 18,000,000 but is at 25,780,667) and never writes the endpoint or key to disk.

## Data

| Path | Content |
|---|---|
| `data/live_snapshot.json` | Output of `live_verify.py --write` on 2026-10-22: addresses, block headers, 22 calls, the log queries of windows A and B and of the post-report rows (on-chain fields only), and the public-source facts |
| `data/window_b_buybacks.json` | The two log queries of the report's buyback-scale range, 8,656 transfers on each leg, one row per log |
| `MANIFEST.json` | SHA-256, size and provenance of both data files |

Everything in `data/` is Ethereum chain data or facts about public pages; no provider's dataset is stored. The two clauses are quoted from the [Sky Atlas](https://github.com/sky-ecosystem/next-gen-atlas) and the implementation note from [forum topic 28153](https://forum.skyeco.com/t/28153). Logs were read through the Etherscan API v2 and calls through an archive node, and only on-chain fields are kept, so another reader gives the same files. Shared helpers are in [`../../lib/evm.py`](../../lib/evm.py).
