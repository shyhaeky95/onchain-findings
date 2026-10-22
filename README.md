# onchain-findings

Reproductions of data errors I reported to public crypto data projects. Each case keeps its inputs, or an extract of them where a provider allows only that, checks them against a manifest, recomputes the numbers in the report, and labels how strong each piece of evidence is.

Code and text here are drafted by an AI agent, which also runs the checks; I review the results and decide what is published. The scripts exist so you don't have to trust either of us: they recompute or re-read every figure they can and label the rest as recorded.

## Evidence levels

| Level | Meaning |
|---|---|
| **Reproduced** | Recomputed here from saved raw inputs listed in the case manifest. |
| **Extract** | Figures copied from saved responses that are not published here, because the provider allowed only an extract. The manifest lists each response's SHA-256, so anyone holding the response can check the extract; for everyone else the figures are shown as `RECORDED`. |
| **Re-read at block (snapshot)** | Read again at the stated block through an archive node; the result is committed with its date, and the case's `live_verify.py --check` repeats the read for anyone with such a node. |
| **Public sources as of a date (snapshot)** | What an API, a repository or a GitHub thread showed on that date. |
| **Arithmetic** | Computed from the rows above; only as strong as its weakest input. |
| **Recorded** | Only the result was kept at the time; the raw response was not. |

Each row is `MATCH` (the evidence supports the report), `ERRATUM` (the report's text is wrong and the evidence equals the documented correction), `RECORDED` (a kept value or extract figure that agrees with the report but is not re-derived; not counted as a match), `OBSERVED` (a fact about the sources after the report; not a statement of the report) or `FAIL`. A checksum only shows that a file has not changed since the manifest was written; each manifest also says where the file came from, or `unknown`.

## Cases

| Case | Report | Upstream outcome | Result |
|---|---|---|---|
| [Uniswap v4 TVL on BSC](findings/20659-defillama-uniswap-v4-bsc/) | [DefiLlama-Adapters#20659](https://github.com/DefiLlama/DefiLlama-Adapters/issues/20659) | No price returned as of 2026-10-21; [#20690](https://github.com/DefiLlama/DefiLlama-Adapters/pull/20690) merged | 12 match · 5 errata · 11 recorded · 4 observed · 0 fail |
| [Rocket Pool megapool deposits](findings/8812-defillama-rocketpool-megapools/) | [dimension-adapters#8812](https://github.com/DefiLlama/dimension-adapters/issues/8812) | Fixed in [#8833](https://github.com/DefiLlama/dimension-adapters/pull/8833), merged 2026-08-18 | 20 match · 0 errata · 3 observed · 0 fail |
| [SKY buybacks and the Atlas burn clause](findings/306-sky-atlas-burn/) | [next-gen-atlas#306](https://github.com/sky-ecosystem/next-gen-atlas/issues/306) | Answered: burned in a later executive vote; spells burned 10/55 of the buybacks on 2026-09-13 and 2026-10-12 | 16 match · 1 erratum · 5 observed · 0 fail |

## Run

Python 3.9 or later, standard library only.

```bash
python3 findings/20659-defillama-uniswap-v4-bsc/reproduce.py
python3 findings/8812-defillama-rocketpool-megapools/reproduce.py
python3 findings/306-sky-atlas-burn/reproduce.py
python3 -m unittest discover -s tests
```

`reproduce.py` runs offline. Each case's `live_verify.py --check` re-reads the on-chain facts through an archive endpoint you provide (`BSC_ARCHIVE_RPC` or `ETH_ARCHIVE_RPC`; the Ethereum cases also take `ETHERSCAN_API_KEY` for wide log ranges). The Ethereum cases share [`lib/evm.py`](lib/evm.py): Keccak-256, ABI encoding and the two log readers.

## Data and license

A case includes data from a provider only as far as the provider allows, and credits and links the source (see each case). The Uniswap v4 case includes an extract of ten DefiLlama figures, not the complete API responses. The Rocket Pool and Sky cases keep only Ethereum chain data, read at fixed blocks, and facts about public pages, with short quotations linked to their source. Rights in that data remain with its providers; the MIT license in [LICENSE](LICENSE) covers the code only.
