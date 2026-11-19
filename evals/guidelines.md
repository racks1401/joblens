# Labeling guidelines (spec 15.2)

Gold labels are written by a human (Ravi). Files in `evals/data/drafts/` are machine drafts with
`"status": "draft"` and must never be copied into gold files without review. A reviewed label is
written to the gold file with `"status": "gold"` and `"labeler": "<your name>"`.

## E1 Retrieval relevance — `evals/data/retrieval_labels.jsonl`

One line per (profile, job) in the pooled list `evals/data/retrieval_pool.jsonl`:

```json
{"profile_id": "p01", "job_key": "greenhouse:12345", "job_id": 412, "relevance": 2, "reason": "SWE intern, python + sql, MCA allowed", "status": "gold", "labeler": "ravi"}
```

| relevance | meaning |
|---|---|
| 2 | Would apply: role and core skills fit, and eligible or likely eligible. |
| 1 | Related but a stretch: partial skill fit, or eligibility unclear. |
| 0 | Not relevant, or clearly ineligible (e.g. senior role needing 5+ years for a fresher). |

Rules:
- Label from the pool file only. It is shuffled and hides which method returned each job and its score.
- Judge against the profile in `evals/data/profiles.jsonl`, not against your own preferences.
- Experience: a fresher profile (0–1 years) gets 0 for roles that require 3+ years, 1 for 1–2 years.
- Location: a job outside the profile's locations and work modes is at most 1.
- Write one short reason per label.
- Consistency check: after a day, re-label a random 10% (`python -m evals.pool --recheck`) and
  report your agreement with yourself.
- `job_key` (`source:source_job_id`) is the stable id; `job_id` is only valid in the DB the pool was built from.

## E2 Extraction — `evals/data/extraction_gold.jsonl`

One line per job, with the posting text snapshot so the eval is reproducible without the DB:

```json
{"job_key": "greenhouse:12345", "job_id": 412, "text": "Title: ...", "gold": {"employment_type": "internship", "work_mode": "remote", "locations": ["Bengaluru"], "duration_months": 6, "pay": {"min": 25000, "max": 25000, "currency": "INR", "period": "month"}, "degrees_allowed": ["BTECH", "MCA"], "specializations": null, "graduation_years": [2026, 2027], "min_cgpa": 7.0, "experience_min_years": null, "fresher_friendly": true, "required_skills": ["python", "sql"], "nice_to_have_skills": null, "apply_deadline": null, "ppo_or_conversion": null}, "status": "gold", "labeler": "ravi"}
```

Rules:
- Every one of the 15 evidenced fields is present; `null` means the posting does not state it.
  Never fill a field from general knowledge (same rule as the extractor).
- Use the normalized forms: degree codes (BTECH, BE, MTECH, MCA, BCA, BSC, MSC, MBA, PHD,
  BACHELORS, MASTERS, ANY_GRADUATE, OTHER), lowercase skills after the synonym map, canonical
  city names (Bengaluru, Gurugram, ...), pay as stated with currency and period (15k → 15000).
- `required_skills`: technical skills the posting requires; soft skills ("communication") are not skills.
- `experience_min_years`: the smallest required number; `0` only if it says freshers / no experience.
- `fresher_friendly`: `true` only if stated (freshers, students, new grads, entry level); otherwise `null`.
- The set has 40 jobs; at least 10 are pasted Indian-style postings (internship forms, campus drives, INR stipends).

## E3 Eligibility — `evals/data/eligibility_labels.jsonl`

```json
{"profile_id": "p01", "job_key": "paste:...", "label": "CHECK", "reason": "CGPA not stated", "status": "gold"}
```

Pairs use jobs from the E2 set so errors can be split into rule bugs vs extraction errors (the runner
re-checks each pair with the gold JobFacts). Label by reading the posting and the profile, applying
spec Section 12 by hand: any hard requirement failed → INELIGIBLE; anything unstated or a soft
mismatch → CHECK; everything stated and met → ELIGIBLE.

## E4 Explanations — human scores

The runner writes `evals/results/explanation_human_sheet.jsonl` with 20 explanations (judge scores
hidden). Fill `groundedness`, `correctness`, `usefulness` (1–5) per line, blind, then re-run the suite to
get the judge-vs-human Spearman correlation.
