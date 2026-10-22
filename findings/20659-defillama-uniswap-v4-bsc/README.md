# Uniswap v4 TVL on BSC: one mispriced token

**Report:** [DefiLlama/DefiLlama-Adapters#20659](https://github.com/DefiLlama/DefiLlama-Adapters/issues/20659), posted 2026-08-24 03:57 UTC, closed 2026-08-25.
**Outcome, as of 2026-10-21:** `coins.llama.fi` returns no price for the token, and [PR #20690](https://github.com/DefiLlama/DefiLlama-Adapters/pull/20690) (merged 2026-08-25) adds it to the BSC blacklist of the `uniswap-v4` adapter. DefiLlama has also revised the published BSC history for 2026-08-22 to 08-24 to about $40M per day.

## What the report showed

Published `uniswap-v4` TVL on BSC went from $40,118,209 (2026-08-22) to $5,750,137,253 (2026-08-24), while daily volume on that chain stayed near $200M. At BSC block 117,739,591 the v4 PoolManager held 125,410,226,751.92 units of a token at `0xac531eb26ca1d21b85126de8fb87e80e09002dcf` whose `name()` and `symbol()` are both `SAND`. It is not Binance-Peg SAND. `coins.llama.fi` priced it at $0.0447 and GeckoTerminal at $1.8e-10, about 2.5e8× apart. At the first price the balance is worth $5.60B; at the second, $22.

## Run

```bash
python3 reproduce.py                                          # offline: no network, no key
BSC_ARCHIVE_RPC=<archive endpoint> python3 live_verify.py --check   # optional: re-read and compare
```

`reproduce.py` checks every data file against `MANIFEST.json`, recomputes each claim, and prints one row per claim. It exits 1 on any FAIL or changed file. `live_verify.py --check` re-reads the on-chain facts at their blocks and compares them with the committed snapshot: exit 1 if a historical read differs, 3 if only the public sources have moved, 4 if a public source could not be read (the historical check still runs). Tests: [`../../tests/test_20659.py`](../../tests/test_20659.py) changes one input for each condition a row checks, in both directions for every erratum, and checks that the row turns into FAIL.

## How to read a row

Each row puts the report's own wording ("stated") next to what the evidence gives ("computed").

| Status | Meaning |
|---|---|
| MATCH | The evidence supports the statement. |
| ERRATUM | The statement is wrong, and the evidence equals the correction documented below, including every fact the correction relies on. If any of those stops holding, or the statement becomes true, the row is FAIL. |
| RECORDED | The value exists here only as a kept result or as a figure in the extract. It agrees with the report, but nothing here re-derives it. Not counted as a match. |
| OBSERVED | A fact about the sources after the fix (O rows). Not a statement of the report, so not counted as a match. |
| FAIL | Anything else. |

| Evidence | Rows | What it rests on |
|---|---|---|
| Extract of saved API responses | R1–R5, R7 | Ten figures, with their timestamps, copied from three DefiLlama API responses saved on 2026-08-24 (see Data below). Shown as RECORDED: the complete responses are not published, so nothing here re-derives these figures. For R1–R3 the endpoint now serves the corrected history (O3). |
| Re-read at block, 2026-10-21 snapshot | L1–L5 | Token name, supply and PoolManager balances read at blocks 117,739,591 and 117,733,791 through an archive node on 2026-10-21. `reproduce.py` reads the stored snapshot, not the chain; re-run `live_verify.py --check` with your own endpoint to test it. Block hashes are stored so the blocks can be checked on any explorer. |
| Public sources as of 2026-10-21 (snapshot) | L6, L7, O1–O4 | The adapter source at two commits, the GitHub issue and PR, and what `coins.llama.fi` and the DefiLlama API served that day. O rows describe the state after the fix; they are not claims of the report. |
| Arithmetic | R6, A1–A9 | Products and ratios of the rows above and of recorded inputs. An arithmetic row is only as strong as its weakest input. |
| Recorded | C1–C5 | Prices and the token enumeration, kept only as results. The figures the report states are fixed in `reproduce.py` from the issue text, and the recorded copies are checked against them. |

Current result for the report's statements: **12 match (4 re-read, 2 public-source, 6 arithmetic), 5 errata, 11 recorded only.** Post-fix observations: 4. Fail: 0. Four of the arithmetic matches (A3, A4, A5, A7) use only recorded inputs, so they check the report's arithmetic, not its facts.

## Errata

The reproduction found five places where the report's text is not supported by the evidence. None changes its conclusion that the published TVL was driven by one mispriced token.

- **E1 (R6).** "Increased about 121×" over 2026-08-22 → 08-24. 121.0× is the 08-22 → 08-23 step; 08-22 → 08-24 is 143.3×. The report's own TVL figures give the same 143.3×.
- **E2 (L5).** The report lists the largest remaining holdings as USDT 12,374,053, USDC 1,138,807, WBNB 290.11 and BTCB 3.57, in a paragraph placed "at BSC block 117,739,591". They come from a 726-page enumeration read with the `latest` tag, so they almost certainly belong to no single block. They match neither block 117,739,591 (12,232,586 · 1,101,876 · 366.55 · 3.58) nor block 117,733,791 (12,374,190 · 1,092,088 · 383.84 · 3.50). Their USDT figure is 137 below the balance at block 117,733,791, consistent with the scan starting near that block. The token facts in the report's table do hold at block 117,739,591 (L1–L4).
- **E3 (A6).** The remaining priced PoolManager total is put "on the order of $17M". The enumeration's own figures give $5,674,885,441 − $5,632,836,336 = $42.0M. $16.7M is a separate read of nine major assets.
- **E4 (A1).** The opening sentence says that at block 117,739,591 the token accounts for about $5.63B. The balance at that block times the report's price is $5.600B, as the report itself says further down. $5.63B is the enumeration's figure, which implies a price of 0.044915; that matches neither recorded price (0.044656, 0.044777).
- **E5 (A8).** "After excluding [the token], the priced BSC PoolManager holdings are about 0.0029× the published BSC TVL." 0.0029× is the nine-asset basket. Over the whole priced remainder it is 0.0073×, still far below the 0.646× of the Ethereum control.

## Limits

- **The DefiLlama figures rest on the author's statement.** They are an extract; the three complete responses are not published (see Data). The protocol endpoint now serves the corrected BSC TVL (O3), so R1–R3 cannot be fetched again; the volume and chain-TVL endpoints were not re-checked. `MANIFEST.json` lists each response's SHA-256 and size, and `python3 reproduce.py --check-extract <dir>` rebuilds `data/extract.json` from responses with those hashes and compares it byte for byte; the author's copies pass. The original run's log, which is private, records the same SHA-256 at fetch time. Public support: the report, posted the same morning, cites the same figures.
- **Timing.** The "2026-08-24" TVL point carries the timestamp 00:51:59Z, about three hours before block 117,739,591 (03:55:26Z); the report's opening sentence sets the two side by side. The volume response was fetched before 03:12Z on 08-24, so its "08-24" point cannot be a full day. The report's "over those same three dates" pairs volume and TVL by DefiLlama's day labels only.
- **No raw RPC responses from August.** The original run read balances with the `latest` tag and stored the head block read just before (117,733,791). Its result file is timestamped 03:11:53Z because that timestamp is taken when the run starts; the head block itself is from 03:11:56Z. The L rows were re-read at fixed blocks on 2026-10-21 instead.
- **The enumeration cannot be re-run from saved data.** Only its totals were kept.
- **Prices are not on chain.** `coins.llama.fi` no longer prices this token, so a live check shows the fixed state. A9 shows that the recorded GeckoTerminal FDV and price are consistent with the on-chain supply, which ties those numbers to this token, but it does not verify the price.
- **Ethereum control.** The report attributes the gap between $413M of major-token holdings and the published $639M to the unenumerated long tail. That attribution was not measured, and hook-contract balances were not excluded.
- **Hook contracts were not measured on BSC either.** The adapter also counts balances held by hooks. The PoolManager-only enumeration reaches 98.7% of the published figure, which bounds that layer, but that is an inference.
- **Live checks need an archive endpoint.** On 2026-10-21, public BSC endpoints refused reads at these blocks (`missing trie node`, a personal-token requirement, or a rate limit). `live_verify.py` checks that an endpoint serves history before using it, and never writes the endpoint to disk.

## Data

| Path | Content |
|---|---|
| `data/extract.json` | Ten figures, with their timestamps, from three DefiLlama API responses served on 2026-08-24 |
| `data/recorded.json` | Values kept only as results, each with its source and what is unknown |
| `data/live_snapshot.json` | Output of `live_verify.py --write` on 2026-10-21 |
| `MANIFEST.json` | SHA-256, size and provenance of every data file |

**Source: [DefiLlama](https://defillama.com).** This case includes 15 figures read from DefiLlama endpoints: the ten in `data/extract.json`, the two `coins.llama.fi` prices in `data/recorded.json` and the three later TVL values in `data/live_snapshot.json`. They are included with DefiLlama's permission, given by email on 2026-10-21, for an extract of the figures used in the report, credited and linked. The complete API responses are not included. The enumeration and basket totals in `data/recorded.json` are the author's sums, priced with `coins.llama.fi`. To reproduce from the source, query the endpoints directly:

- [`api.llama.fi/protocol/uniswap-v4`](https://api.llama.fi/protocol/uniswap-v4) (R1–R3, R7)
- [`api.llama.fi/summary/dexs/uniswap-v4?dataType=dailyVolume&excludeTotalDataChartBreakdown=false`](https://api.llama.fi/summary/dexs/uniswap-v4?dataType=dailyVolume&excludeTotalDataChartBreakdown=false) (R4)
- [`api.llama.fi/v2/historicalChainTvl/BSC`](https://api.llama.fi/v2/historicalChainTvl/BSC) (R5)

DefiLlama has since corrected this history. On 2026-10-21 the first endpoint served about $40M a day of uniswap-v4 TVL on BSC for 2026-08-22 to 08-24 (O3), not the figures above; the other two endpoints were not re-checked. Rights in the data remain with DefiLlama; the MIT license covers the code only.

**Other sources.** The price, FDV and 24-hour volume in C2 are [GeckoTerminal](https://www.geckoterminal.com)'s, read from `api.geckoterminal.com` during the original run. C5 is [CoinGecko](https://www.coingecko.com)'s price for The Sandbox, cited in the report as `coingecko:the-sandbox`. The request that returned it was not recorded; that key format is the one `coins.llama.fi` uses, so C5 may be a sixteenth figure served by DefiLlama.
