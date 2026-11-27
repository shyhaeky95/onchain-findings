# HYPE in the Assistance Fund: the governance burn and the Info API's `totalSupply`

**Statements checked:** the Hyper Foundation's [proposal](https://x.com/HyperFND/status/2001127850754367525) (X, 2025-12-17) and [result](https://x.com/HyperFND/status/2003684257949188396) (X, 2025-12-24) of a validator vote on the Assistance Fund's HYPE, and two pages of the Hyperliquid docs, [Fees](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees) and [HIP-1](https://hyperliquid.gitbook.io/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-1-native-token-standard) (read 2026-11-27).
**Checked against:** Hyperliquid's public Info API (`api.hyperliquid.xyz/info`), read on 2026-11-26 at 09:56:10 UTC, and CoinGecko's total supply in a response captured one second later.

## What this case shows

On 2025-12-17 the Hyper Foundation proposed a validator vote *"to formally recognize the Assistance Fund HYPE as burned, removing the tokens permanently from the circulating and total supply"*, adding that *"No onchain action is required, as the tokens are already in a system address with no private key"* (P1). The same post says what the vote is: *"By voting “Yes,” validators agree to treat the Assistance Fund HYPE as burned"* and *"This vote is binding social consensus to never authorize a protocol upgrade to access this address"* (P2). It does not mention the Info API. On 2025-12-24 the Foundation announced that the HYPE at the Assistance Fund address `0xfefefefefefefefefefefefefefefefefefefefe` *"has been formally recognized as burned"* (P3). On 2026-11-27 the Fees page of the Hyperliquid docs said: *"HYPE in the assistance fund is burned, removing the tokens permanently from the circulating and total supply."* (P4).

On 2026-11-26 the Info API's `tokenDetails` for HYPE gave a `maxSupply` of 1,000,000,000 and a `totalSupply` of 998,670,601.38, a difference of 1,329,398.62 HYPE (L1). The Assistance Fund held 49,345,806.71 HYPE, in the same response and in the fund's own spot state (L2). The same response lists 94,023 genesis balances that add up to `maxSupply`, none of them the fund's, with no genesis allocation through another token (G1); the HIP-1 page defines `maxSupply` as *"the maximum and initial supply"* and says *"The supply may decrease over time"* (P5). Under that accounting model — `totalSupply` is the genesis total less what has been taken out of it, and nothing has been added since — at most 1,329,398.62 HYPE has been taken out of `totalSupply`, from any cause, so at least 48,016,408.09 of the fund's 49,345,806.71 HYPE (97.31%) is still counted in it (A1). The same response leaves the fund out of `circulatingSupply`, which equals `totalSupply` minus `futureEmissions` minus four non-circulating balances, the fund's among them (L3). The zero address and `0x…dead` are among those four too (L4).

CoinGecko's `total_supply`, in a response captured at 09:56:11 UTC whose `last_updated` was 76 seconds before the Info read, was 949,784,555.37 HYPE: 48,886,046.01 below the API's figure, a difference 459,760.71 smaller than the fund's balance at that moment (X1).

The governance decision and the Info API may use different supply-accounting conventions. The vote is a commitment by validators not to authorize a protocol upgrade that would give access to the fund's address; the API reports a supply aggregate whose implementation is not established here. Under the model above, the API's `totalSupply` on 2026-11-26 still counted substantially all of the fund, while the Foundation's proposal and the docs describe the fund's HYPE as removed from total supply. This case does not say which figure is correct, or that the vote was not carried out.

## Related work

The same difference has been described before this case: UnderHyper's [Assistance Fund explainer](https://underhyper.com/learn/assistance-fund-and-hype-buybacks/) (updated 2026-11-04) notes that the API lists the fund's HYPE as a non-circulating balance and still includes it in `totalSupply`, and Crypto Burn Tracker's [note on the Assistance Fund burn](https://cryptoburntracker.com/research/hyperliquid-assistance-fund-burn) (published 2026-11-18, updated 2026-11-22) compares the API's figure of about 998.7 million with CoinGecko's of about 950 million. What this case adds is the saved responses, the lower bound and its premise, and the checks that make them re-runnable.

## Run

```bash
python3 reproduce.py                  # offline: no network, no key
python3 live_verify.py --check        # optional: read the API and the Fees page now; no key
```

`reproduce.py` checks the data files against `MANIFEST.json`, checks that each saved Info response has the SHA-256, request body and capture time recorded in `data/public_sources.json`, recomputes each figure and prints one row per statement. It exits 1 on any FAIL or changed file. Those records were written by the author at capture (`extract.py` shows how), so they show the files are consistent with each other, not that the API returned these bytes.

The public Info queries take no historical block or timestamp, so the 2026-11-26 responses cannot be read again through them, and the figures move every day as the fund keeps buying HYPE. `live_verify.py --check` is therefore a regression check: it reads the current state and checks that it still has the shape the case relies on — the genesis conditions of G1, the supply figures in order and finite, the circulating identity, each non-circulating address once, the fund listed with a positive balance confirmed by its spot state, neither the fund's balance nor `maxSupply − totalSupply` below the 2026-11-26 figure, and the fund's balance at least ten times `maxSupply − totalSupply` (a conservative alert meaning, under A1's model, that at least 90% of the fund is still counted; 37.1 times on 2026-11-26) — and that the Fees page, identified by its title, still has the quoted sentence in its visible text. It prints the current lower bound. Exit codes, in order of precedence: **2** a read failed, a response was unusable, the saved snapshot failed its manifest, only the two reads of the fund's balance disagree by more than 1,000 HYPE (an operational tolerance, not evidence that both reads saw the same state), or `--save` could not write; **1** a usable response breaks a relation; **3** the relations hold but the identified Fees page no longer has the sentence; **4** the relations hold but the page could not be read or identified; **0** all hold. `--save DIR` writes the responses as new files outside `data/`; `--coingecko` also prints CoinGecko's current figure, without changing the exit code.

Tests: [`../../tests/test_hyperliquid_af.py`](../../tests/test_hyperliquid_af.py) changes the saved inputs, keeping the integrity records consistent. Every named condition of every row has a mutation that breaks that condition and no other in its row, and the suite checks this; a few further mutations break related conditions together. The tests exercise the comparisons implemented here; they do not establish that the saved responses are what the API returned, or what its fields mean.

## How to read a row

Each row puts the case's wording ("stated") next to what the evidence gives ("computed").

| Status | Meaning |
|---|---|
| MATCH | The evidence in `data/` supports the statement, for every condition the row lists. |
| RECORDED | The statement rests on a source that is not stored here: quotations of the posts and the docs pages, or an extract of a CoinGecko response. The conditions check only that the capture record says what the case says. Not counted as a match. |
| FAIL | Anything else. The row's note names the condition that does not hold. |

| Evidence | Rows | What it rests on |
|---|---|---|
| Reproduced (saved Info API responses, 2026-11-26 09:56:10 UTC) | G1, L1–L4 | `tokenDetails` for HYPE and `spotClearinghouseState` for the fund's address, each read once and saved as returned. |
| Arithmetic | A1 | The fund's balance against `maxSupply − totalSupply`, under the model of G1 and P5. |
| Public sources as of 2026-11-26 / 2026-11-27 (snapshot) | P1–P5, R1 | The two posts, read through X's syndication endpoint and api.fxtwitter.com, and the two docs pages. Stored as the quoted sentences, where each was found (in the pages' visible text), and the SHA-256 of each response, not as copies. Each post's time also follows from its ID (03:10:33.434 and 04:28:48.405 UTC). |
| Extract (CoinGecko, 2026-11-26) | X1 | Two fields of CoinGecko's `/coins/hyperliquid` response, copied as text; the response is listed by SHA-256 and not published. |

Current result: **6 match (5 reproduced, 1 arithmetic), 7 recorded (6 public-source, 1 extract). Fail: 0.**

## Limits

- **The Info responses rest on the author's capture.** They were read once and cannot be fetched from the API again for that moment; the hashes, request bodies and times in `data/public_sources.json` were recorded by the same author.
- **A1 is a bound under an accounting model, not a fact about the implementation.** It assumes that the reported genesis allocations and `totalSupply` are on the same basis, that whatever is taken out of supply reduces `totalSupply`, and that nothing has been added since genesis. G1 and the HIP-1 definition (P5) support the model; the saved response does not establish it, and the Hyperliquid docs do not describe how the Info API computes `totalSupply`. `live_verify.py` flags a net increase in reported `totalSupply` against the snapshot, but cannot rule out an addition offset by a larger removal.
- **`maxSupply − totalSupply` is a difference between two API fields.** This case does not reconstruct which operations make it up, or whether any part of it is the fund's.
- **`totalSupply` includes `futureEmissions`** (409,611,423.61 HYPE not yet emitted on 2026-11-26), which `circulatingSupply` leaves out. L3 uses the field as the API returns it.
- **When the fund was first listed as non-circulating** is not checked here. For the zero address and `0x…dead` (L4), whether they are inside `totalSupply` cannot be told from these figures: together they hold less than `maxSupply − totalSupply`.
- **The quotations are recorded, not stored.** The posts were read through two third-party readers, which agree on account and time; the syndication reader returns only the first 279 characters of the proposal, so its later sentences were found in the full text only. The docs pages were read a day after the Info responses.
- **The vote shares are the Foundation's** (R1). This case has no verifiable snapshot of validator stake at 2025-12-24 04:00 UTC, and the replies in the governance forum were not counted here.
- **"Never had a private key"** is the Foundation's statement (P2). Nothing here checks a key.
- **How CoinGecko derives `total_supply` is not checked here.**

## Data

| Path | Content |
|---|---|
| `data/info_tokenDetails_HYPE.json.gz` | Info `tokenDetails` for HYPE, as returned (5,203,717 bytes), gzipped; includes the genesis balance list used by G1. |
| `data/info_spotClearinghouseState_AF.json` | Info `spotClearinghouseState` for `0xfefe…fefe`, as returned; only the HYPE entry (token 150) is used. |
| `data/public_sources.json` | The two posts (links, quoted sentences, which reader returned each, response SHA-256), the two docs pages (links, titles, quoted sentences, page SHA-256), two CoinGecko fields, and the request bodies, capture time and SHA-256 of the two Info responses. Written by `extract.py` from the saved responses. |
| `MANIFEST.json` | SHA-256, size and provenance of the three data files. |

The Info responses come from Hyperliquid's public API and are stored as returned. The posts and the docs pages are linked above and quoted in short. CoinGecko: two fields of its public API; [data provided by CoinGecko](https://www.coingecko.com/en/coins/hyperliquid). No CoinGecko response is stored.
