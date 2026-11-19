### Keyword query experiment (not production)

Assumption: unlabeled retrieved jobs count as not relevant. Small, noisy sample.

#### Deciding set: all pool pairs, includes LLM drafts (134 pairs: 49 human + 85 qwen drafts)

| Variant | nDCG@10 | MRR | P@5 | unlabeled in top-10 |
|---|---|---|---|---|
| keyword / current | 0.446 | 0.267 | 0.560 | 0/50 |
| keyword / role_boost | 0.369 | 0.190 | 0.400 | 16/50 |
| hybrid / current | 0.519 | 0.423 | 0.680 | 6/50 |
| hybrid / role_boost | 0.422 | 0.375 | 0.520 | 11/50 |

nDCG@10 per profile: keyword / current: {'p00': 0.345, 'p05': 0.442, 'p09': 0.329, 'p10': 0.518, 'p15': 0.595}; keyword / role_boost: {'p00': 0.22, 'p05': 0.416, 'p09': 0.153, 'p10': 0.53, 'p15': 0.524}; hybrid / current: {'p00': 0.628, 'p05': 0.535, 'p09': 0.314, 'p10': 0.641, 'p15': 0.478}; hybrid / role_boost: {'p00': 0.485, 'p05': 0.481, 'p09': 0.143, 'p10': 0.592, 'p15': 0.408}

#### Sanity check: p00 human labels only (24 pairs)

| Variant | nDCG@10 | MRR | P@5 | unlabeled in top-10 |
|---|---|---|---|---|
| keyword / current | 0.345 | 0.333 | 0.600 | 0/10 |
| keyword / role_boost | 0.220 | 0.200 | 0.200 | 6/10 |
| hybrid / current | 0.628 | 1.000 | 0.800 | 1/10 |
| hybrid / role_boost | 0.485 | 1.000 | 0.400 | 3/10 |

