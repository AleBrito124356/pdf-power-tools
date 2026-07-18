"""Form inspection, fill and flatten tests."""

from __future__ import annotations

import json
import os

from pdftools import ops_forms


def test_list_fields(form_pdf):
    fields = {f["name"]: f for f in ops_forms.list_fields(form_pdf)}
    assert "full_name" in fields
    assert fields["full_name"]["type"] == "text"
    assert fields["email"]["type"] == "text"
    assert fields["subscribe"]["type"] == "button"
    assert fields["country"]["type"] == "choice"
    assert "Mexico" in fields["country"]["options"]


def test_fill_from_dict(form_pdf, out_dir):
    out = os.path.join(out_dir, "filled.pdf")
    ops_forms.fill(
        form_pdf, out,
        {"full_name": "Ada Lovelace", "email": "ada@example.com", "country": "Mexico"},
    )
    values = {f["name"]: f["value"] for f in ops_forms.list_fields(out)}
    assert values["full_name"] == "Ada Lovelace"
    assert values["email"] == "ada@example.com"
    assert values["country"] == "Mexico"


def test_fill_from_json_file(form_pdf, out_dir):
    data_path = os.path.join(out_dir, "data.json")
    with open(data_path, "w", encoding="utf-8") as fh:
        json.dump({"full_name": "Alan Turing"}, fh)
    out = os.path.join(out_dir, "filled_json.pdf")
    ops_forms.fill(form_pdf, out, data_path)
    values = {f["name"]: f["value"] for f in ops_forms.list_fields(out)}
    assert values["full_name"] == "Alan Turing"


def test_fill_and_flatten(form_pdf, out_dir):
    from pdftools import ops_content

    out = os.path.join(out_dir, "flat.pdf")
    ops_forms.fill(form_pdf, out, {"full_name": "Katherine Johnson"}, flatten=True)
    # After flattening the interactive fields are gone...
    assert ops_forms.list_fields(out) == []
    # ...but the value is baked into the page and still visible/selectable.
    assert "Katherine Johnson" in ops_content.extract_text(out)
