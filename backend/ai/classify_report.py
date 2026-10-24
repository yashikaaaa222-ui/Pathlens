"""Report text classifier: TF-IDF + Logistic Regression, with a keyword fallback.

The classifier only ever suggests. A person picks the category when it is unsure, and a moderator makes
the final call. It is deliberately small and interpretable.

Honest limits, repeated in the API so nobody over-reads them:
* It is trained on a small SYNTHETIC seed set (ai/seed_reports.py), so the cross-validated score below is
  optimistic. It says nothing about accuracy on real citizen reports.
* When it is not confident it says so (`needs_review`) instead of guessing.
* If it fails for any reason, `classify_text` returns "needs review" and report intake keeps working.
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import FeatureUnion, Pipeline

from ai.seed_reports import SEED

log = logging.getLogger("pathlens.ai")

CONFIDENCE_THRESHOLD = 0.45  # below this the model abstains and the report goes to "needs review"
MIN_TEXT_CHARS = 4

# Keyword rules, used only when the model abstains.
RULES: list[tuple[str, re.Pattern]] = [
    ("streetlight", re.compile(r"street ?light|lamp ?post|light (is |was )?(out|broken|not working)|bulb|fused", re.I)),
    ("poor_lighting", re.compile(r"\bdark\b|unlit|poorly lit|no lights?|\bdim\b", re.I)),
    ("obstruction", re.compile(r"block|obstruct|debris|fallen|pothole|construction|barrier|parked|stall|encroach", re.I)),
    ("footpath", re.compile(r"footpath|pavement|sidewalk|broken tiles?|uneven|slab|crack", re.I)),
    ("visibility", re.compile(r"visib|overgrown|bush|tree|hedge|fog|blind (turn|corner)", re.I)),
]


@dataclass
class Classification:
    category: Optional[str]
    confidence: Optional[float]
    method: str  # "model" | "rules" | "none"
    needs_review: bool
    probabilities: dict[str, float] = field(default_factory=dict)


def _pipeline() -> Pipeline:
    features = FeatureUnion(
        [
            ("word", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, lowercase=True)),
            # Character n-grams make it robust to typos and spelling variants.
            ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), sublinear_tf=True, lowercase=True)),
        ]
    )
    return Pipeline([("tfidf", features), ("lr", LogisticRegression(C=10.0, max_iter=2000))])


class ReportClassifier:
    def __init__(self, seed: Optional[list[tuple[str, str]]] = None):
        data = seed or SEED
        self.texts = [t for t, _ in data]
        self.labels = [y for _, y in data]
        self.pipe = _pipeline().fit(self.texts, self.labels)
        self.classes: list[str] = list(self.pipe.named_steps["lr"].classes_)
        self.info = self._evaluate()

    def _evaluate(self) -> dict:
        """Cross-validated macro-F1 on the seed set, reported with a warning attached."""
        info = {
            "model": "TF-IDF (word + character n-grams) with Logistic Regression",
            "classes": self.classes,
            "trained_on": len(self.texts),
            "trained_on_is_synthetic": True,
            "confidence_threshold": CONFIDENCE_THRESHOLD,
            "cv_macro_f1": None,
            "note": (
                "Trained on a small synthetic seed set written by one author, so the score is optimistic and "
                "says nothing about accuracy on real reports. The classifier only suggests; a person or a "
                "moderator decides."
            ),
        }
        try:
            n_splits = min(5, min(self.labels.count(c) for c in set(self.labels)))
            if n_splits >= 2:
                cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=7)
                pred = cross_val_predict(_pipeline(), self.texts, self.labels, cv=cv)
                info["cv_macro_f1"] = round(float(f1_score(self.labels, pred, average="macro")), 3)
        except Exception:  # evaluation is informational; never block start-up on it
            log.exception("Could not cross-validate the classifier")
        return info

    def classify(self, text: str) -> Classification:
        text = (text or "").strip()
        if len(text) < MIN_TEXT_CHARS:
            return Classification(None, None, "none", True)
        proba = self.pipe.predict_proba([text])[0]
        probs = {c: round(float(p), 3) for c, p in zip(self.classes, proba)}
        best = int(np.argmax(proba))
        conf = float(proba[best])
        if conf >= CONFIDENCE_THRESHOLD:
            return Classification(self.classes[best], round(conf, 3), "model", False, probs)
        for cat, rx in RULES:
            if rx.search(text):
                return Classification(cat, None, "rules", False, probs)
        return Classification(None, round(conf, 3), "none", True, probs)


_instance: Optional[ReportClassifier] = None
_lock = threading.Lock()


def get_classifier() -> ReportClassifier:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = ReportClassifier()
    return _instance


def classify_text(text: str) -> Classification:
    """Never raises. If the classifier is unavailable the report simply goes to "needs review"."""
    try:
        return get_classifier().classify(text)
    except Exception:
        log.exception("Classifier unavailable; falling back to needs-review")
        return Classification(None, None, "none", True)


def classifier_info() -> Optional[dict]:
    try:
        return get_classifier().info
    except Exception:
        return None
