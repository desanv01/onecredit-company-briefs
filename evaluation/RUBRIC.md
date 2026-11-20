# Paired review rubric

The briefs are for an analyst deciding what to check before vetting a supplier or project partner. Review each brief against the source record as of 31 December 2025. Accept equivalent wording and sensible rounding. A brief can surface other relevant facts supported by the record.

The manifest was frozen from the source records before inspecting generated briefs. Use the eight development cases for prompt and rule tuning. Use the 12 disjoint held-out cases for the paired comparison. The held-out sample was chosen to exercise specific failure modes, so its scores describe these cases. The manifest records the SHA-256 of the raw Windows checkout and the raw LF repository copy to accommodate Git line-ending settings.

## Run the review

From the checkout root, use the project Python environment. Create the output directory first if needed. The optional run log uses the output filenames without their extensions as version names.

```bash
python -m company_briefs.evaluate data/companies.jsonl summaries/baseline.jsonl summaries/evidence.jsonl --packet .runs/paired-review.md
```

Add `--run-log PATH` for telemetry and candidate trace checks. The command reports JSONL/schema errors, source and held-out coverage, and median word count on stderr. With logs, it also reports measured telemetry, missing log entries and candidate trace errors. The candidate check validates its saved selection, resolves its source pointers and re-renders the saved brief using the current code. A resolvable source pointer can still support a poorly chosen fact or an incorrect interpretation. Run trace checks with the same code revision used to generate the summaries. A nonzero exit signals an input error, invalid JSONL or trace, or a missing brief on the held-out set.

Read both briefs and the original record. Score A and B independently, recording a short reason and the source field or case number for material errors and omissions. Keep the version key closed until the scores and preference are written down. Formatting can still make a version recognizable.

```bash
python -m company_briefs.evaluate data/companies.jsonl summaries/baseline.jsonl summaries/evidence.jsonl --reveal --packet .runs/review-key.md
```

## Anchors

| Dimension | 0 | 1 | 2 |
| --- | --- | --- | --- |
| Factual fidelity | A material claim is wrong or unsupported | Minor imprecision with no material change in meaning | Claims and calculations are supported by the record |
| Due-diligence priorities | Misses the main fact affecting whether or what to investigate | Covers the main issue but omits relevant context or buries it | Leads with the facts most likely to change the analyst's next step |
| Caveats | A missing period, legal role, status or audit caveat is misstated or conveyed misleadingly | Preserves the essential caveat with a minor omission | Distinguishes the record's scope, timing, attribution and uncertainty wherever needed |
| Readability and next step | Difficult to scan or advises action unsupported by the record | Understandable with some clutter or a generic follow-up | Easy to scan, with a specific, proportionate follow-up when warranted |

Flag a **critical error** separately if an unsupported statement or omission could materially change the triage decision. Examples include omitting a pending winding-up petition, treating a claim as an award or payment, describing a plaintiff claim as defendant exposure, and inferring a trend from one financial year. For cases without material issues, a concise overview can earn the top score without a follow-up.

Record an overall preference of A, B or tie for each pair. Prefer the brief without a critical error if only one has such an error. Then use the dimension scores and write a one-sentence reason for the preference. For a missing or malformed brief, record it as unavailable and exclude the pair from quality comparisons while counting it in coverage.

Report how many pairs were reviewed, which version was preferred or tied, the critical error counts by version and a few sourced examples. Report fallback counts separately when logs are available. The evaluator produces no human scores or preferences.
