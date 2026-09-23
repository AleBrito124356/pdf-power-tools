"""Form filling that validates and coerces instead of silently dropping data."""

from __future__ import annotations

import os

import pytest
from pypdf import PdfReader

from pdftools import ops_forms
from pdftools._util import PdfToolsError, PdfToolsWarning


def _values(path):
    return {f["name"]: f["value"] for f in ops_forms.list_fields(path)}


@pytest.mark.parametrize("value", [True, "yes", "on", "true", "Yes", "/Yes", 1])
def test_checkbox_truthy_values_use_the_real_on_state(form_pdf, out_dir, value):
    out = os.path.join(out_dir, "cb.pdf")
    ops_forms.fill(form_pdf, out, {"subscribe": value})
    assert _values(out)["subscribe"] == "/Yes"


@pytest.mark.parametrize("value", [False, "no", "off", "false", 0])
def test_checkbox_falsy_values_switch_off(form_pdf, out_dir, value):
    on = os.path.join(out_dir, "on.pdf")
    ops_forms.fill(form_pdf, on, {"subscribe": True})
    off = os.path.join(out_dir, "off.pdf")
    ops_forms.fill(on, off, {"subscribe": value})
    assert _values(off)["subscribe"] == "/Off"


def test_list_fields_reports_checkbox_states(form_pdf):
    fields = {f["name"]: f for f in ops_forms.list_fields(form_pdf)}
    assert fields["subscribe"]["states"] == ["/Yes"]


def test_unknown_key_warns_with_suggestion_and_fills_the_rest(form_pdf, out_dir):
    out = os.path.join(out_dir, "typo.pdf")
    with pytest.warns(PdfToolsWarning, match="did you mean 'email'"):
        ops_forms.fill(form_pdf, out, {"full_name": "Ada", "emial": "a@b.c"})
    assert _values(out)["full_name"] == "Ada"


def test_strict_refuses_and_writes_nothing(form_pdf, out_dir):
    out = os.path.join(out_dir, "strict.pdf")
    with pytest.raises(PdfToolsError, match="emial"):
        ops_forms.fill(form_pdf, out, {"emial": "a@b.c"}, strict=True)
    with pytest.raises(PdfToolsError, match="not an option"):
        ops_forms.fill(form_pdf, out, {"country": "Brazil"}, strict=True)
    assert not os.path.exists(out)


def test_choice_values_are_validated_and_normalised(form_pdf, out_dir):
    out = os.path.join(out_dir, "choice.pdf")
    ops_forms.fill(form_pdf, out, {"country": "mexico"})
    assert _values(out)["country"] == "Mexico"
    with pytest.warns(PdfToolsWarning, match="not an option"):
        ops_forms.fill(form_pdf, out, {"country": "Atlantis"})
    assert _values(out)["country"] == "Panama"  # left at its default


def test_radio_group_selects_one_button(radio_form_pdf, out_dir):
    out = os.path.join(out_dir, "radio.pdf")
    ops_forms.fill(radio_form_pdf, out, {"size": "M", "qty": 3})
    values = _values(out)
    assert values["size"] == "/M"
    assert values["qty"] == "3"
    states = [
        a.get_object().get("/AS")
        for a in PdfReader(out).pages[0]["/Annots"]
        if a.get_object().get("/Parent") is not None
    ]
    assert states.count("/M") == 1 and states.count("/Off") == 2
    with pytest.warns(PdfToolsWarning, match="several options"):
        ops_forms.fill(radio_form_pdf, out, {"size": True})


def test_fill_bad_json_is_a_clean_error(form_pdf, out_dir):
    bad = os.path.join(out_dir, "bad.json")
    with open(bad, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    with pytest.raises(PdfToolsError, match="not valid JSON"):
        ops_forms.fill(form_pdf, os.path.join(out_dir, "x.pdf"), bad)


def test_fill_needs_a_form(text_pdf, out_dir):
    with pytest.raises(PdfToolsError, match="no interactive form"):
        ops_forms.fill(text_pdf, os.path.join(out_dir, "x.pdf"), {"a": "b"})
