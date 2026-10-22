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

Each row is `MATCH` (the evidence supports the report), `ERRATUM` (the report's text is wrong and the evidence equals the documented correction), `RECORDED` (a kept value or extract figure that agrees with the report but is not re-derived; not counted as a match), `OBSERVED` (a fact about the sources after the fix; not a statement of the report) or `FAIL`. A checksum only shows that a file has not changed since the manifest was written; each manifest also says where the file came from, or `unknown`.

## Cases

| Case | Report | Upstream outcome | Result |
|---|---|---|---|
| [Uniswap v4 TVL on BSC](findings/20659-defillama-uniswap-v4-bsc/) | [DefiLlama-Adapters#20659](https://github.com/DefiLlama/DefiLlama-Adapters/issues/20659) | No price returned as of 2026-10-21; [#20690](https://github.com/DefiLlama/DefiLlama-Adapters/pull/20690) merged | 12 match · 5 errata · 11 recorded · 4 observed · 0 fail |

## Run

Python 3.9 or later, standard library only.

```bash
python3 findings/20659-defillama-uniswap-v4-bsc/reproduce.py
python3 -m unittest discover -s tests
```

## Data and license

A case includes data from a provider only as far as the provider allows, and credits and links the source (see each case). The Uniswap v4 case includes an extract of ten DefiLlama figures, not the complete API responses. Rights in that data remain with its providers; the MIT license in [LICENSE](LICENSE) covers the code only.
