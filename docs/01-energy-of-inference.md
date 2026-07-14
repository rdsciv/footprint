# The Energy of Inference

*Part 1 of the [footprint](../README.md) deep-dive series. The numbers here are the ones behind [`coefficients.json`](../modelfootprint/coefficients.json); derivations live in [METHODOLOGY.md](../METHODOLOGY.md).*

Training gets the headlines, but inference is where AI's energy story now lives. Inference accounts for roughly 80–90% of total AI compute today, and industry projections put it at ~75% of total AI energy demand by 2030. Every prompt you send is a marginal draw on that system. This doc explains what one prompt physically costs, why the honest answer is a *range*, and why the range is narrowing.

## What a prompt costs: the public anchors

Three kinds of numbers exist in public, in increasing order of rigor:

1. **Executive statements.** Sam Altman's widely-quoted figure: an average ChatGPT query uses **~0.34 Wh** — one kWh serves ~2,900 queries. No methodology attached, but it's a useful sanity anchor from someone who can see the meter.
2. **Independent benchmarks.** The "How Hungry is AI?" benchmark series (arXiv:2505.09598) measured per-token energy across hardware classes, from sub-Wh small-model queries up to **~29 Wh for the most expensive long-prompt configurations**. University of Rhode Island's estimates put GPT-5-class queries at **18.35 Wh typical, up to 40 Wh**. Epoch AI's bottom-up FLOP model lands near **~0.3 Wh** for GPT-4o-scale chat.
3. **Provider disclosures.** The most important recent development. Google's measured disclosure (arXiv:2508.15734) puts the **median Gemini Apps text prompt at 0.24 Wh, 0.26 mL of water, and 0.03 gCO2e** (market-based) — *full stack*, meaning accelerator + host CPU + idle reserve capacity + datacenter overhead. Mistral published an audited lifecycle analysis: **1.14 gCO2e and 45 mL of water per 400-token response** from Mistral Large 2 (scope includes amortized training). 

These spread across two orders of magnitude — 0.24 Wh to 40 Wh per query — and all of them are simultaneously plausible, because "a query" is not a unit of physics.

## The physics: three token classes, three prices

What actually determines energy is **which tokens, on which model, with what serving efficiency**. LLM inference has two phases with different physical character:

- **Prefill** (reading your input): compute-bound, massively parallel, efficient per token.
- **Decode** (writing the output): memory-bandwidth-bound, sequential, roughly **3× the energy per token** of prefill across every source we reviewed.

And there's a third class most calculators ignore: **cache reads**. Agentic tools like Claude Code resend an enormous, mostly-unchanged context on every turn. Providers store the computed state (KV cache) and re-reading it costs a fraction of computing it fresh — the pricing discount (5–10× cheaper than fresh input) is the best public proxy for the energy discount. In real coding-agent sessions we measured, **cache reads are 90%+ of all tokens**. Any per-prompt estimate that prices them as fresh input overstates agent footprints several-fold; any that ignores input entirely understates chat footprints.

This is why `footprint` computes energy as:

```
energy_Wh = (tok_in · e_in + tok_cache · e_cache + tok_out · e_out) × PUE
```

with per-1k-token coefficients tiered by model class (small / mid / frontier), each carrying a `{central, low, high}` range and a `measured|modeled|speculative` label. The frontier tier's central output coefficient (0.9 Wh/1k tokens) is ~6× the small tier's (0.15) — which is the entire quantitative basis for the "use a smaller model when you can" advice in [doc 05](./05-which-model-when.md).

## Overhead: the datacenter tax

The GPU is not the whole bill. Google's full-stack accounting found total energy runs **~1.7× accelerator-only energy** (range 1.3–2.5×): host CPUs, networking, idle capacity provisioned for reliability, and facility overhead (PUE — modern hyperscale runs ~1.08–1.4, we use 1.2 central). When you see a per-query figure, always ask: chip-only or full-stack? The 33× drop in Google's own median-prompt figure over roughly a year came partly from model efficiency (quantization, distillation, speculative decoding, MoE routing) and partly from better accounting of what "a prompt" uses — efficiency is real and fast-moving, which is why every coefficient in this project is versioned and dated.

## Why per-token linearity is a simplification (and when it breaks)

METHODOLOGY.md §2.4 documents where the linear abstraction bends:

- **Batching**: the same token costs less energy at high batch occupancy; providers batch aggressively and you can't observe your batch.
- **Speculative decoding** discounts output tokens by an unobservable factor.
- **Reasoning/thinking tokens** are billed as output and cost full decode energy — a "short answer" from a reasoning model can be a long computation.
- **Long-context attention** grows superlinearly at extreme context lengths.

We keep the linear model anyway, because it is the best *verifiable-from-outside* abstraction, and we widen the uncertainty band (2.5–3×) to cover what we cannot see. The alternative — false precision — is how environmental calculators lose credibility.

## The aggregate picture

Individual prompts are small; the industry is not. Global datacenter electricity demand is projected to exceed **1,000 TWh in 2026** (Gartner) — roughly Japan's annual consumption — with AI the dominant growth driver. US grid interconnection queues passed **1,500 GW** in 2025; new high-capacity connections in major hubs face **4–7 year waits**; Texas alone logged **438,595 MW** of data-center connection requests. Energy is the largest recurring operating expense of inference infrastructure, which is why *where* and *when* compute runs is becoming an optimization layer of its own — the subject of [doc 02](./02-duck-curve-and-grid.md).

## What this means for you

- A chat message is **~0.1–1 Wh** — a few seconds of a microwave. Guilt is not the right frame for individual light usage.
- An agentic coding session is **hundreds of Wh** — the statusline in this repo routinely shows 100–400 Wh sessions. That's a phone charge every few minutes of heavy agent use: still small individually, but big enough that *model choice and cache hygiene visibly matter*.
- The honest unit is the **token, priced by class and model tier, with a range** — which is exactly what the statusline and `/footprint` show.

## Sources

- Sam Altman, "The Gentle Singularity" (June 2025) — 0.34 Wh/query anchor
- Google, "Measuring the environmental impact of delivering AI at Google Scale" — [arXiv:2508.15734](https://arxiv.org/abs/2508.15734)
- "How Hungry is AI? Benchmarking Energy, Water, and Carbon Footprint of LLM Inference" — [arXiv:2505.09598](https://arxiv.org/abs/2505.09598)
- Epoch AI, "How much energy does ChatGPT use?" (2025)
- Mistral AI, "Our contribution to a global environmental standard for AI" (audited LCA, July 2025)
- University of Rhode Island AI lab, GPT-5 energy estimates (2025)
- Gartner datacenter power forecasts; ERCOT/Texas interconnection reporting
- Full derivation and uncertainty treatment: [METHODOLOGY.md](../METHODOLOGY.md) §2, §5
