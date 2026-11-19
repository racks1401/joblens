"""Scam text classifier (TF-IDF + calibrated LogisticRegression), trained by ml/train_scam_classifier.py.

Trained on the Kaggle "Real or Fake Job Posting" (EMSCAD) data: old (2012-2014) and mostly US/GB postings.
Its probability on Indian / ATS postings is NOT validated, so its delta is capped (classifier_max) and it never
by itself marks a posting as a scam.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import ROOT, get_settings


def model_path() -> Path:
    p = Path(get_settings().cfg.trust.classifier_path)
    return p if p.is_absolute() else ROOT / p


@lru_cache
def load_model(path: str | None = None) -> Any | None:
    """The fitted sklearn pipeline, or None if it has not been trained yet."""
    p = Path(path) if path else model_path()
    if not p.exists():
        return None
    import joblib

    return joblib.load(p)


def scam_probability(text: str, model: Any | None = None) -> float | None:
    m = model if model is not None else load_model()
    if m is None:
        return None
    return float(m.predict_proba([text])[0][1])
