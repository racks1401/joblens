"""Train the scam text classifier (spec 13.1) on data/raw/fake_job_postings.csv.

    python -m ml.train_scam_classifier

TF-IDF (word 1-2 grams, max 50,000 features, min_df 3) + LogisticRegression(class_weight="balanced"),
probabilities calibrated with CalibratedClassifierCV. Stratified 80/10/10 split, fixed seed. The decision
threshold is chosen on the validation split (best F1); all reported numbers are on the untouched test split.
Plain accuracy is not a headline metric (always saying "real" scores ~95%).

Data caveat: Kaggle "Real or Fake Job Posting Prediction" (EMSCAD), postings from 2012-2014, ~60% US and
~13% GB, ~1.5% India. Scores on our Indian / ATS postings are NOT validated by this test split.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import ROOT

SEED = 20261119
TEXT_FIELDS = ["title", "company_profile", "description", "requirements", "benefits"]


def load_rows(path: Path) -> tuple[list[str], list[int], list[str]]:
    csv.field_size_limit(10**9)
    texts, ys, locs = [], [], []
    with path.open(encoding="utf-8", errors="replace", newline="") as f:
        for r in csv.DictReader(f):
            texts.append("\n".join((r.get(k) or "") for k in TEXT_FIELDS))
            ys.append(int(r["fraudulent"]))
            locs.append((r.get("location") or "").split(",")[0].strip())
    return texts, ys, locs


def build_pipeline(min_df: int = 3, max_features: int = 50_000, cv: int = 5) -> Any:
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline

    lr = LogisticRegression(class_weight="balanced", max_iter=2000, random_state=SEED)
    return Pipeline([
        ("tfidf", TfidfVectorizer(ngram_range=(1, 2), max_features=max_features, min_df=min_df, sublinear_tf=True,
                                  strip_accents="unicode", lowercase=True)),
        ("clf", CalibratedClassifierCV(lr, method="sigmoid", cv=cv)),
    ])


def threshold_metrics(y: list[int], p: list[float], thr: float) -> dict[str, float]:
    tp = sum(1 for a, b in zip(y, p, strict=True) if a and b >= thr)
    fp = sum(1 for a, b in zip(y, p, strict=True) if not a and b >= thr)
    fn = sum(1 for a, b in zip(y, p, strict=True) if a and b < thr)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return {"threshold": thr, "precision": prec, "recall": rec, "f1": 2 * prec * rec / (prec + rec) if prec + rec else 0.0,
            "tp": tp, "fp": fp, "fn": fn}


def train(texts: list[str], ys: list[int], min_df: int = 3, cv: int = 5) -> tuple[Any, dict[str, Any]]:
    from sklearn.model_selection import train_test_split

    from evals import metrics as M

    x_tr, x_rest, y_tr, y_rest = train_test_split(texts, ys, test_size=0.2, stratify=ys, random_state=SEED)
    x_va, x_te, y_va, y_te = train_test_split(x_rest, y_rest, test_size=0.5, stratify=y_rest, random_state=SEED)
    model = build_pipeline(min_df=min_df, cv=cv).fit(x_tr, y_tr)
    p_va = list(model.predict_proba(x_va)[:, 1])
    best = max((threshold_metrics(y_va, p_va, t / 100) for t in range(5, 96)), key=lambda m: (m["f1"], -m["threshold"]))
    p_te = list(model.predict_proba(x_te)[:, 1])
    metrics = {
        "split": {"train": len(y_tr), "val": len(y_va), "test": len(y_te), "test_fraud": sum(y_te)},
        "test_pr_auc": M.average_precision(y_te, p_te),
        "test_at_val_threshold": threshold_metrics(y_te, p_te, best["threshold"]),
        "test_at_0.5": threshold_metrics(y_te, p_te, 0.5),
        "test_recall_at_precision_0.8": M.recall_at_precision(y_te, p_te, 0.8),
        "val_best_threshold": best["threshold"],
    }
    return model, metrics


def main() -> None:
    import joblib

    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(ROOT / "data" / "raw" / "fake_job_postings.csv"))
    ap.add_argument("--out", default=str(ROOT / "models" / "scam_clf.joblib"))
    args = ap.parse_args()
    texts, ys, locs = load_rows(Path(args.csv))
    model, metrics = train(texts, ys)
    n = len(ys)
    top: dict[str, int] = {}
    for loc in locs:
        top[loc or "(none)"] = top.get(loc or "(none)", 0) + 1
    metrics["data"] = {"rows": n, "fraudulent": sum(ys), "fraud_rate": sum(ys) / n,
                       "share_US": top.get("US", 0) / n, "share_GB": top.get("GB", 0) / n, "share_IN": top.get("IN", 0) / n,
                       "caveat": "Kaggle EMSCAD data, 2012-2014, mostly US/GB; not validated on Indian or ATS postings."}
    metrics["trained_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, args.out)
    Path(args.out).with_suffix(".metrics.json").write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    print(json.dumps(metrics, indent=1))


if __name__ == "__main__":
    main()
