### E1 Retrieval (indicative only)

**Small sample, noisy numbers.** One reviewer; 5 profiles; at most ~25 labels per profile. Differences of a few points between methods are not meaningful.

Pairs outside the labeled set count as not relevant (relevance 0); 'unlabeled in top-10' shows how often that assumption is used.

#### (a) Human-reviewed labels only

Only pairs a human reviewed (status human_reviewed).

Labeled pairs per profile: p00: 24, p05: 5, p09: 5, p10: 7, p15: 8 (total 49).

| Method | P@5 | P@10 | nDCG@10 | MRR | unlabeled in top-10 |
|---|---|---|---|---|---|
| keyword | 0.200 | 0.180 | 0.272 | 0.142 | 33/50 |
| vector (minilm, structured) | 0.200 | 0.180 | 0.273 | 0.200 | 30/50 |
| hybrid RRF (keyword + minilm structured) | 0.280 | 0.220 | 0.372 | 0.273 | 33/50 |

#### (a1) p00 only, human labels (24 labeled pairs)

| Method | P@5 | P@10 | nDCG@10 | MRR | unlabeled in top-10 |
|---|---|---|---|---|---|
| keyword | 0.600 | 0.600 | 0.345 | 0.333 | 0/10 |
| vector (minilm, structured) | 0.800 | 0.700 | 0.783 | 1.000 | 0/10 |
| hybrid RRF (keyword + minilm structured) | 0.800 | 0.700 | 0.628 | 1.000 | 1/10 |

One profile, 10 results per method: a sanity check, not a deciding set.

#### (b) All pool pairs — includes LLM drafts

Human label where it exists (49 pairs), otherwise the qwen draft (85 pairs, status draft, NOT hand-labeled).

Labeled pairs per profile: p00: 24, p05: 24, p09: 29, p10: 29, p15: 28 (total 134).

| Method | P@5 | P@10 | nDCG@10 | MRR | unlabeled in top-10 |
|---|---|---|---|---|---|
| keyword | 0.560 | 0.520 | 0.446 | 0.267 | 0/50 |
| vector (minilm, structured) | 0.520 | 0.460 | 0.468 | 0.350 | 0/50 |
| hybrid RRF (keyword + minilm structured) | 0.680 | 0.540 | 0.519 | 0.423 | 6/50 |
