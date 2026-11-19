### Trust: scam text classifier (P5, spec 13.1)

**Data caveat:** Kaggle "Real or Fake Job Posting Prediction" (EMSCAD), postings from 2012-2014; 17,880 rows,
866 fraudulent (4.8%); 60% US, 13% GB,
1.5% India. **These numbers do not validate the classifier on our Indian / ATS postings.**

Model: TF-IDF (word 1-2 grams, 50k features, min_df 3) + LogisticRegression(class_weight=balanced), sigmoid-calibrated
(CalibratedClassifierCV, cv=5). Stratified 80/10/10 split, fixed seed; threshold chosen on validation.

| Held-out test split | Value |
|---|---|
| Test size (fraudulent) | 1,788 (87) |
| Threshold (chosen on validation) | 0.51 |
| Precision (fraud) | 0.959 (71 TP, 3 FP) |
| Recall (fraud) | 0.816 (16 missed) |
| F1 (fraud) | 0.882 |
| PR-AUC | 0.907 |
| Recall at precision >= 0.8 | 0.851 |

Use in the trust score: delta = -round(40 x scam_prob), never a scam verdict by itself. On the 113 in-scope board jobs the
median scam_prob is 0.001 (max 0.052). On an Indian-style "guaranteed placement + Rs 45,000 training fee" post (synthetic,
written by Claude) it gave 0.07 - the rules, not the classifier, flagged it. E6 (30 hand-collected Indian examples) is still pending.
