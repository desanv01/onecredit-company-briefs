# Company briefs for supplier and partner vetting

## What a good summary is for this team, and why

The reader is an analyst deciding what to inspect before vetting a supplier or project partner. A good brief names the business and recorded status, leads with the facts that might change the next step, and makes the age and limits of the record visible. It should give an analyst a specific follow-up when one is warranted. Straightforward companies can stay straightforward.

Priority comes from the reviewing task. A winding-up status, unusual audit opinion, or pending defendant claim can deserve more space than a full list of directors. A missing profit number is an evidence gap. A claim amount is the amount sought, and the disposition is a separate field. Those distinctions matter more to a first-read decision than fluent prose. The input is a synthetic snapshot dated 31 December 2025. All 47 companies with financial statements have FY2024 as their latest year.

## What you built

I compared two approaches on all 50 [source records](data/companies.jsonl) using official DeepSeek `deepseek-flash` with thinking disabled and temperature zero.

1. **[Direct baseline](summaries/baseline.jsonl).** A first-read prompt gives the model the full JSON record and asks it for concise Markdown. The prompt includes guidance for materiality, periods, units, and legal roles and outcomes. During development I tightened the instructions after seeing a brief infer the direction of performance beyond the last reported year.
2. **[Evidence-backed candidate](summaries/evidence.jsonl).** Python extracts profile, financial, audit, ownership, and litigation facts. Each retains a local ID and paths to the raw record. The extractor calculates material operating-margin changes for consecutive years with matching year-end month and day. DeepSeek sees the compact evidence and returns an ordered list of IDs and an optional supported follow-up ID. Python validates the selection, enforces required facts, and renders the source-derived wording. The retry and fallback paths are bounded and recorded.

The [batch runner](company_briefs/batch.py) checkpoints each company and ties a run directory to the dataset, model, prompt versions, and generation source hashes. Both JSONL versions work in the supplied viewer. [Run identity](evaluation/run-identity.json) and [outcome log](evaluation/run-log.jsonl) preserve the frozen configuration and actual selections, usage, and timing. The generation code was frozen at commit `3ebe65960e3912a8914614b4ccdf0d456a0713e9`.

I used Codex and its GPT-6.2 Sol workers for research, implementation, verification, and drafting. The official DeepSeek API generated the baseline text and the candidate selections. Two GPT-6.2 Sol reviewers scored six paired cases each against the source records. The judgments below are AI-assisted.

## How you know whether it's good

The [case manifest](evaluation/manifest.json) assigns eight development cases and 12 disjoint review cases. The review set and its source expectations were frozen before reviewing generated outputs. It includes ordinary records, missing and single-year financial histories, audit exceptions, winding-up cases, and both litigation roles. The [rubric](evaluation/RUBRIC.md) scores four dimensions from 0 to 2 and flags critical errors separately. The reviewers received A/B packets with version labels masked. The writing formats could still make the version recognizable.

| Held-out review on 12 pairs | Direct baseline | Evidence-backed |
| --- | ---: | ---: |
| Preferred briefs | 2 | 10 |
| Briefs with at least one critical error | 5 | 0 |
| Factual fidelity, points out of 24 | 9 | 24 |
| Due-diligence priorities, points out of 24 | 15 | 19 |
| Caveats, points out of 24 | 15 | 22 |
| Readability and next step, points out of 24 | 18 | 21 |

There were zero ties. [Per-case scores and reasons](evaluation/paired-review.json) include the version key and error flags. These are judgments on a small, deliberately varied set, with one reviewer per pair. The full run establishes coverage and trace validity. The paired review estimates quality on the sampled cases.

- **Where evidence selection helped.** For `SYN-001212`, a baseline follow-up asked whether a RM9.47m petition judgment remained unpaid. `CASE-2021-3977` records RM9,468,400 as the amount claimed and a status of judgment entered. The record provides no award amount or payment history. The candidate kept the claim and recorded status distinct and directed the analyst to the current filings and orders.
- **Where the direct baseline helped.** For `SYN-000546`, the direct brief identified that operating cash flow turned positive while the company still had an after-tax loss in FY2023. It connected that sequence to a specific question about the FY2024 recovery. The candidate's fact set focused on the latest cash flow and the two reported profit figures, so that timing relationship was unavailable to its selector.

The offline evaluator checked 50 unique summaries per version and validated all 50 evidence selections, source pointers, and re-rendered briefs. That establishes consistency between the saved text and its extraction trace. Source-based review covers interpretation and materiality, which the trace alone cannot establish.

| Full run metric | Direct baseline | Evidence-backed |
| --- | ---: | ---: |
| Successful calls | 50/50 | 50/50 |
| Retries / fallbacks | 0 / 0 | 0 / 0 |
| Input tokens | 100,984 | 27,267 |
| Output tokens | 17,248 | 1,260 |
| Median per-company elapsed time | 3.16 s | 0.73 s |
| Median brief length | 192 words | 104.5 words |
| Estimated API cost at assumed uncached rates | US$0.051 | US$0.010 |

Elapsed time is local per-company generation time from a four-worker batch. The cost illustration applies US$0.30 per million input tokens and US$1.20 per million output tokens uniformly. It excludes development calls and does not use billing or cache-hit data. The direct prompt targeted about 120–170 words and exceeded that at the median. Many evidence briefs used fewer than 120 words. Brevity was helpful for scanning, but the selected facts sometimes lacked context the direct version provided.

## What you'd do next, and how you'd keep improving it

I would start with a reviewer-labeled set covering routine and difficult cases and add a second reviewer on a subset to measure agreement. I would separately assess whether an analyst can choose the same follow-up more quickly. The current results support an evidence-backed first read on this synthetic sample. The next measurement should test the analyst workflow itself.

The most useful content change is a small set of cash-flow and profit recovery comparisons, with explicit year pairs and source paths. I would build these against the development set and evaluate on fresh cases. If a recurring litigation omission appears, I would add a fact that captures the omitted detail and rerun the same role, status, and claim checks.

A deterministic selection ablation would quantify whether the model adds material value once the fact list and coverage rules are fixed. I would compare it to the current candidate on fresh blind pairs, then keep the least expensive selection method that meets the analyst's priorities.
