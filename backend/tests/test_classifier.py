import pytest

from ai import classify_report
from ai.classify_report import CONFIDENCE_THRESHOLD, RULES, Classification, ReportClassifier, classify_text, get_classifier
from services.risk_engine import CATEGORIES


@pytest.fixture(scope="module")
def clf():
    return get_classifier()


@pytest.mark.parametrize(
    "text,expected",
    [
        ("The lamp near my building has stopped working", "streetlight"),
        ("Whole street is unlit and dark at night", "poor_lighting"),
        ("Tree has fallen across the road", "obstruction"),
        ("Footpath slabs cracked and broken", "footpath"),
        ("Overgrown bush at the blind corner", "visibility"),
    ],
)
def test_unseen_descriptions_are_classified_correctly(clf, text, expected):
    r = clf.classify(text)
    assert r.category == expected and r.method == "model" and not r.needs_review
    assert r.confidence >= CONFIDENCE_THRESHOLD


@pytest.mark.parametrize("text", ["Nice morning today", "asdf qwerty", "I lost my wallet"])
def test_unrelated_text_goes_to_review_instead_of_guessing(clf, text):
    r = clf.classify(text)
    assert r.category is None and r.needs_review


def test_empty_or_tiny_text_is_not_classified(clf):
    assert clf.classify("").needs_review and clf.classify("ok").category is None


def test_the_model_info_is_honest_about_its_limits(clf):
    info = clf.info
    assert info["trained_on_is_synthetic"] is True
    assert info["trained_on"] >= 80 and set(info["classes"]) == set(CATEGORIES)
    assert info["cv_macro_f1"] is not None and info["cv_macro_f1"] > 0.7
    assert "optimistic" in info["note"]


def test_keyword_rules_back_up_a_model_that_abstains():
    clf = ReportClassifier()

    class Unsure:
        def predict_proba(self, _texts):
            return [[1 / len(clf.classes)] * len(clf.classes)]

    clf.pipe = Unsure()
    r = clf.classify("hedge near the school")
    assert r.category == "visibility" and r.method == "rules" and r.confidence is None
    assert clf.classify("zzzz qqqq xxxx").needs_review


def test_every_rule_points_at_a_real_category():
    assert {c for c, _ in RULES} <= set(CATEGORIES)


def test_classify_text_never_raises_even_if_the_classifier_breaks(monkeypatch):
    def boom():
        raise RuntimeError("model file missing")

    monkeypatch.setattr(classify_report, "get_classifier", boom)
    r = classify_text("street light is out")
    assert isinstance(r, Classification) and r.needs_review and r.category is None
