"""ML foundations: config, features, calibration, model, selection."""

from __future__ import annotations

import numpy as np
import pytest

from app.classification.config_loader import ConfigError, default_config_dir
from app.classification.schemas import Document, ExistingLabel
from ml.classification.calibration import PlattScaler, sigmoid
from ml.classification.config import load_ml_config
from ml.classification.features import FeatureBuilder, content_text, filename_text
from ml.classification.model import MLModel
from ml.classification.selection import cross_validate, select_c
from tests.helpers import mkdoc

SEED = 7


def doc(text, filename="a.txt", labels=()):
    return Document(content=text, filename=filename, extension=filename.rsplit(".", 1)[-1],
                    existing_labels=[ExistingLabel(scheme="s", value=v) for v in labels])  # fmt: skip


# ---- config ---------------------------------------------------------------------------------
def test_real_ml_config_loads_and_records_no_operating_point(bundle):
    cfg, digest = load_ml_config(bundle.policy)
    assert cfg.level_head.C == 0.3 and cfg.category_head.C == 1.0 and len(digest) == 64
    assert cfg.decision.category_threshold == 0.5 and cfg.decision.category_thresholds == {}
    assert cfg.threshold_for("PII") == 0.5


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("taxonomy_version: 1.0.0", "taxonomy_version: 9.0.0", "taxonomy"),
        ("class_weight: balanced\n  max_iter: 2000\ncategory_head", "class_weight: heavy\n  max_iter: 2000\ncategory_head", "class_weight"),
        ("method: sigmoid", "method: isotonic", "sigmoid"),
        ("category_thresholds: {}", "category_thresholds: {NOPE: 0.4}", "unknown categories"),
        ("category_thresholds: {}", "category_thresholds: {PII: 1.5}", "within"),
        ("c_grid: [0.3, 1.0, 3.0, 10.0]", "c_grid: [0.3, -1.0]", "positive"),
    ],
)  # fmt: skip
def test_ml_config_rejects_bad_values(bundle, tmp_path, old, new, message):
    import shutil

    d = tmp_path / "config"
    shutil.copytree(default_config_dir(), d)
    p = d / "ml/ml.v1.yaml"
    text = p.read_text()
    assert old in text
    p.write_text(text.replace(old, new, 1))
    with pytest.raises((ConfigError, ValueError), match=message):
        load_ml_config(bundle.policy, d)


# ---- features -------------------------------------------------------------------------------
def test_filename_tokens_and_extension_block():
    assert (
        filename_text(doc("x", "Acquisition_Targets_2027.xlsx"))
        == "acquisition targets 2027 xlsx ext_xlsx"
    )


def test_content_is_truncated():
    assert len(content_text(doc("x" * 500), 100)) == 100


def test_embedded_labels_are_not_features(bundle):
    cfg, _ = load_ml_config(bundle.policy)
    train = [doc("alpha beta gamma delta " * 3, f"f{i}.txt") for i in range(4)]
    fb = FeatureBuilder(cfg.features).fit(train)
    a = fb.transform([doc("alpha beta gamma", "f1.txt", labels=["STRICTLY CONFIDENTIAL"])])
    b = fb.transform([doc("alpha beta gamma", "f1.txt")])
    assert (a != b).nnz == 0


def test_vocabulary_comes_only_from_the_fitted_documents(bundle):
    cfg, _ = load_ml_config(bundle.policy)
    fb = FeatureBuilder(cfg.features).fit(
        [doc("common shared words appear here " * 2, f"x{i}.txt") for i in range(3)]
    )
    names = set(fb.word_feature_names())
    assert "common" in names and "zebra" not in names
    assert fb.blocks["word"][0] == 0 and fb.n_features == fb.blocks["filename"][1]


def test_filename_block_can_be_disabled(bundle):
    cfg, _ = load_ml_config(bundle.policy)
    off = cfg.features.model_copy(update={"filename_block": False})
    fb = FeatureBuilder(off).fit([doc("alpha beta gamma " * 3, f"n{i}.txt") for i in range(3)])
    assert "filename" not in fb.blocks


# ---- calibration ----------------------------------------------------------------------------
def test_platt_learns_a_monotone_map_and_reports_its_parameters():
    rng = np.random.default_rng(0)
    scores = np.concatenate([rng.normal(-2, 1, 200), rng.normal(2, 1, 200)])
    y = np.concatenate([np.zeros(200), np.ones(200)])
    s = PlattScaler(5, 5).fit(scores, y)
    p = s.transform(np.array([-3.0, 0.0, 3.0]))
    assert s.fitted and p[0] < 0.1 < 0.9 < p[2] and p[0] < p[1] < p[2]
    assert s.describe()["fitted"] and s.describe()["n_positive"] == 200


def test_platt_falls_back_and_says_it_is_uncalibrated_when_data_is_too_thin():
    s = PlattScaler(5, 5).fit(np.array([0.1, -0.2, 0.3, 0.4]), np.array([0, 0, 1, 0]))
    assert not s.fitted and s.describe()["a"] is None
    assert s.transform(np.array([0.0]))[0] == pytest.approx(0.5)  # raw sigmoid of the score


def test_sigmoid_is_bounded():
    v = sigmoid(np.array([-1e9, 0.0, 1e9]))
    assert v[0] == pytest.approx(0, abs=1e-9) and v[1] == 0.5 and v[2] == pytest.approx(1, abs=1e-9)


# ---- model ----------------------------------------------------------------------------------
LEVELS = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "HIGHLY_CONFIDENTIAL"]
CATS = ["PII", "PHI", "FINANCIAL_PCI", "SOURCE_CODE", "CREDENTIALS_SECRETS", "INTELLECTUAL_PROPERTY", "TRADE_SECRET", "MA_CORP_STRATEGY"]  # fmt: skip


def corpus(n_per=12):
    """A small separable corpus: vocabulary determines level and one category."""
    vocab = {
        "PUBLIC": ("brochure announcement launch", []),
        "INTERNAL": ("cafeteria schedule holiday", []),
        "CONFIDENTIAL": ("compiler module function", ["SOURCE_CODE"]),
        "HIGHLY_CONFIDENTIAL": ("patient diagnosis medication", ["PHI"]),
    }
    docs, levels, cats = [], [], []
    for lv, (words, cs) in vocab.items():
        for i in range(n_per):
            docs.append(
                doc(f"{words} {words} note {i} report{i % 3} " * 2, f"{lv[:3].lower()}_{i % 3}.txt")
            )
            levels.append(lv)
            cats.append(cs)
    return docs, levels, cats


@pytest.fixture(scope="module")
def fitted(bundle):
    cfg, _ = load_ml_config(bundle.policy)
    docs, levels, cats = corpus()
    m = MLModel(cfg, LEVELS, CATS)
    m.fit_heads(docs, levels, cats)
    m.calibrate(docs, levels, cats)
    return m, docs, levels, cats


def test_model_predicts_a_separable_corpus_and_probabilities_are_valid(fitted):
    m, docs, levels, cats = fitted
    pred = m.predict(docs)
    assert pred.level_probs.shape == (48, 4) and np.allclose(pred.level_probs.sum(1), 1.0)
    assert [LEVELS[i] for i in pred.level_probs.argmax(1)] == levels
    assert ((pred.category_probs >= 0) & (pred.category_probs <= 1)).all()
    j = CATS.index("PHI")
    assert pred.category_probs[levels.index("HIGHLY_CONFIDENTIAL"), j] > 0.5
    assert pred.category_probs[levels.index("PUBLIC"), j] < 0.5


def test_calibration_flags_reflect_data_sufficiency(fitted):
    m, *_ = fitted
    pred = m.predict(corpus()[0][:2])
    assert pred.level_calibrated is True
    assert (
        pred.category_calibrated["PHI"] is True and pred.category_calibrated["SOURCE_CODE"] is True
    )
    assert (
        pred.category_calibrated["PII"] is False
    )  # no positives anywhere: never claimed calibrated


def test_model_is_deterministic(bundle):
    cfg, _ = load_ml_config(bundle.policy)
    docs, levels, cats = corpus()

    def run():
        m = MLModel(cfg, LEVELS, CATS)
        m.fit_heads(docs, levels, cats)
        m.calibrate(docs, levels, cats)
        return m.predict(docs)

    a, b = run(), run()
    assert np.array_equal(a.level_probs, b.level_probs) and np.array_equal(
        a.category_probs, b.category_probs
    )


def test_explanations_show_only_short_alphabetic_words_never_digits_or_emails(bundle):
    cfg, _ = load_ml_config(bundle.policy)
    docs, levels, cats = corpus()
    docs = [
        doc(
            d.content + " ana.smith@corp.example 912-34-5678 4111111111111111 " + "x" * 40,
            d.filename,
        )
        for d in docs
    ]
    m = MLModel(cfg, LEVELS, CATS)
    m.fit_heads(docs, levels, cats)
    m.calibrate(docs, levels, cats)
    words = m.explain_level(docs[-1], "HIGHLY_CONFIDENTIAL") + m.explain_category(docs[-1], "PHI")
    assert words
    for token, weight in words:
        assert (
            token.replace(" ", "").isalpha()
            and len(token) <= cfg.evidence.max_token_chars
            and weight > 0
        )
    assert {"patient", "diagnosis", "medication"} & {t for t, _ in words}


def test_explanation_for_an_unmodelled_category_is_empty(fitted):
    m, docs, *_ = fitted
    assert m.explain_category(docs[0], "PII") == [] or m.cat_lrs["PII"] is not None


# ---- selection ------------------------------------------------------------------------------
def family_corpus():
    """Label is determined by a family-unique word: memorisable, but not generalisable."""
    items = []
    levels = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "HIGHLY_CONFIDENTIAL"]
    for f in range(12):
        lv = levels[f % 4]
        for i in range(6):
            items.append(mkdoc(f"d{f}_{i}", level=lv, group=f"fam{f}", split="train",
                               content=f"uniqueword{f}x alpha{f}y filler text number {i} for family {f} " * 3,
                               filename=f"fam{f}_{i}.txt"))  # fmt: skip
    return items


def test_grouped_cv_does_not_reward_memorising_families(bundle):
    cfg, _ = load_ml_config(bundle.policy)
    small = cfg.model_copy(
        update={"selection": cfg.selection.model_copy(update={"c_grid": [1.0], "cv_folds": 4})}
    )
    rows = cross_validate(small, family_corpus(), LEVELS, CATS)
    assert rows[0]["level_macro_f1"] < 0.6  # unseen families carry unseen words: near chance


def test_select_c_prefers_the_higher_score_and_breaks_ties_toward_smaller_c():
    rows = [{"C": 0.3, "k": 0.5}, {"C": 1.0, "k": 0.7}, {"C": 3.0, "k": 0.7}, {"C": 10.0, "k": 0.6}]
    assert select_c(rows, "k") == 1.0
    assert select_c([{"C": 0.3, "k": 0.5}, {"C": 3.0, "k": 0.5}], "k") == 0.3


def test_discriminative_digit_and_email_tokens_are_never_shown_as_evidence(bundle):
    """The masking filter must be exercised: make identifier-like tokens the TOP features."""
    cfg, _ = load_ml_config(bundle.policy)
    docs, levels, cats = corpus()
    docs = [
        doc(
            d.content
            + (
                " leak12345 leak@corp.example 912345678 " * 5 if lv == "HIGHLY_CONFIDENTIAL" else ""
            ),
            d.filename,
        )
        for d, lv in zip(docs, levels, strict=True)
    ]
    m = MLModel(cfg, LEVELS, CATS)
    m.fit_heads(docs, levels, cats)
    names = set(m.features.word_feature_names())
    assert {"leak12345", "912345678"} <= names, (
        "precondition: the identifier tokens are real features"
    )
    shown = [t for t, _ in m.explain_level(docs[-1], "HIGHLY_CONFIDENTIAL")]
    assert shown and all(t.replace(" ", "").isalpha() for t in shown)
    assert not any("leak" in t and any(ch.isdigit() for ch in t) for t in shown)
