"""Template engine: slots, choices, memoisation, evidence spans."""

from __future__ import annotations

import pytest

from evals.classification.dataset.rng import DetRandom
from evals.classification.dataset.template import (
    TemplateError,
    extract_spans,
    referenced_slots,
    render,
    render_many,
)

POOLS = {
    "first_name": ["Ana", "Ben", "Cy", "Di", "Ed"],
    "last_name": ["Cole", "Diaz", "Egan", "Frye"],
    "company_prefix": ["Alder", "Birch", "Cedar", "Dunn", "Elm"],
    "company_suffix": ["Labs", "Systems", "Foods"],
    "personal_domain": ["mailbox.example"],
    "codeword": ["Falcon", "Granite"],
    "color": ["red", "green", "blue", "amber", "violet", "teal", "grey", "pink"],
}


def r(template, seed="t"):
    return render(template, DetRandom(seed), POOLS)


def test_same_slot_is_memoised_and_tag_gives_independent_draw():
    text = r("«color» «color» «color#2» «color#2»").text.split()
    assert text[0] == text[1] and text[2] == text[3]


def test_tagged_draws_are_distinct_from_earlier_draws():
    for seed in range(40):
        a, b = r("«color» «color#2»", seed).text.split()
        assert a != b


def test_person_and_company_attributes():
    out = r("«person» / «person.first» «person.last» / «person.email» / «person.workemail»").text
    person, first_last, email, workemail = out.split(" / ")
    assert person == first_last
    first, last = person.split(" ")
    assert email == f"{first.lower()}.{last.lower()}@mailbox.example"
    company_out = r("«company» / «company.domain» / «company.short» / «person.workemail»").text
    company, domain, short, work = company_out.split(" / ")
    assert domain == company.lower().replace(" ", "") + ".example"
    assert short == company.split(" ")[0]
    assert work.endswith("@" + domain)  # work email uses the document's company


def test_choice_and_empty_alternative():
    seen = {r("«a|b|c»", s).text for s in range(60)}
    assert seen == {"a", "b", "c"}
    assert {r("x«, y|»z", s).text for s in range(60)} == {"x, yz", "xz"}


def test_nested_tokens_expand_innermost_first():
    assert r("«Hi «color»|Hi «color»»").text.startswith("Hi ")


def test_filler_generators_are_fresh_unless_tagged():
    fresh = {r("«@int:1-999» «@int:1-999»", s).text for s in range(30)}
    assert any(a != b for a, b in (x.split() for x in fresh))
    tagged = r("«@int:1-999#a» «@int:1-999#a»").text.split()
    assert tagged[0] == tagged[1]


def test_identity_generators_stay_consistent_within_a_document():
    a, b = r("«@ssn» «@ssn»").text.split()
    assert a == b
    c1, c2 = r("«@card:plain» «@card:plain#2»").text.split()
    assert c1 != c2  # an explicit tag requests an independent draw


def test_evidence_spans_have_exact_offsets():
    rendered = r("Name: «person.first}. SSN ⟦PII§«@ssn»⟧ end ⟦PHI§«color»⟧.".replace("}", "»"))
    assert rendered.spans[0].label == "PII" and rendered.spans[1].label == "PHI"
    for sp in rendered.spans:
        assert rendered.text[sp.char_start : sp.char_end] == sp.text
    assert "⟦" not in rendered.text and "§" not in rendered.text


def test_multiline_spans_and_offsets_after_earlier_markers():
    rendered = extract_spans("a ⟦X§line1\nline2⟧ b ⟦Y§zz⟧ c")
    assert rendered.text == "a line1\nline2 b zz c"
    assert [(s.label, s.text) for s in rendered.spans] == [("X", "line1\nline2"), ("Y", "zz")]
    for sp in rendered.spans:
        assert rendered.text[sp.char_start : sp.char_end] == sp.text


@pytest.mark.parametrize(
    "bad",
    [
        "⟦X§never closed",
        "no label ⟦just text⟧",
        "⟦X§a ⟦Y§nested⟧ b⟧",
        "stray ⟧ close",
        "lone § sign",
    ],
)
def test_malformed_markers_are_rejected(bad):
    with pytest.raises(TemplateError):
        extract_spans(bad)


@pytest.mark.parametrize(
    "bad",
    [
        "«nosuchpool»",
        "«@nosuchgen»",
        "«person.bogus»",
        "«color.first»",
        "«unbalanced",
        "stray » close",
    ],
)
def test_unknown_or_unbalanced_tokens_are_rejected(bad):
    with pytest.raises(TemplateError):
        r(bad)


def test_render_many_shares_slots_across_templates():
    out = render_many(
        {"filename": "«company».txt", "body": "Dear «company»"}, DetRandom("m"), POOLS
    )
    assert out["filename"].text.removesuffix(".txt") == out["body"].text.removeprefix("Dear ")


def test_rendering_is_deterministic():
    tpl = "«person» «@card» ⟦PII§«@ssn»⟧ «a|b|c» «company»"
    assert r(tpl, "same") == r(tpl, "same")
    assert r(tpl, "same") != r(tpl, "other")


def test_referenced_slots_ignores_choices():
    assert referenced_slots("«person» «a|b» «@ssn» «person.first»") == {
        "person",
        "@ssn",
        "person.first",
    }
