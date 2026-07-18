"""AcroForm operations: inspect, fill, and flatten interactive forms.

Reading fields and filling values is handled by pypdf. Flattening (baking the
filled values in so they can no longer be edited) needs a reasonably recent
pypdf and is detected at runtime.
"""

from __future__ import annotations

import inspect
import json
import os
from typing import Any, Dict, List, Optional, Union

from pypdf import PdfReader, PdfWriter

from ._util import PdfToolsError, ensure_parent_dir


_FIELD_TYPES = {
    "/Tx": "text",
    "/Btn": "button",
    "/Ch": "choice",
    "/Sig": "signature",
}


def list_fields(input_path: str) -> List[Dict[str, Any]]:
    """Return every form field as a record.

    Each record has ``name``, ``type`` (text / button / choice / signature),
    ``value`` (current value or ``None``) and ``options`` (for choice fields,
    otherwise an empty list). Returns an empty list for documents with no form.
    """
    reader = PdfReader(input_path)
    fields = reader.get_fields() or {}
    records: List[Dict[str, Any]] = []
    for name, field in fields.items():
        raw_type = getattr(field, "field_type", None)
        friendly = _FIELD_TYPES.get(raw_type, "unknown")
        value = getattr(field, "value", None)
        options: List[str] = []
        try:
            opt = field.get("/Opt")
        except Exception:
            opt = None
        if opt:
            for entry in opt:
                if isinstance(entry, (list, tuple)) and entry:
                    options.append(str(entry[-1]))
                else:
                    options.append(str(entry))
        records.append(
            {
                "name": str(name),
                "type": friendly,
                "value": None if value is None else str(value),
                "options": options,
            }
        )
    return records


def _strip_interactive_form(writer: PdfWriter) -> None:
    """Remove widget annotations and the AcroForm so no field stays editable."""
    try:
        writer.remove_annotations(subtypes="/Widget")
    except Exception:  # pragma: no cover - older pypdf
        pass
    try:
        root = writer._root_object  # noqa: SLF001 - stable pypdf internal
        if "/AcroForm" in root:
            del root["/AcroForm"]
    except Exception:  # pragma: no cover - defensive
        pass


def _load_data(data: Union[Dict[str, Any], str]) -> Dict[str, Any]:
    if isinstance(data, dict):
        return data
    if isinstance(data, str):
        if not os.path.isfile(data):
            raise PdfToolsError(f"Form data file not found: {data!r}")
        with open(data, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if not isinstance(loaded, dict):
            raise PdfToolsError("Form data JSON must be an object of field:value.")
        return loaded
    raise PdfToolsError("data must be a dict or a path to a JSON file.")


def fill(
    input_path: str,
    output: str,
    data: Union[Dict[str, Any], str],
    *,
    flatten: bool = False,
) -> str:
    """Fill form fields from a dict or a JSON file.

    Keys are field names (see :func:`list_fields`); values are strings for text
    fields or the export value for checkboxes/choices. Unknown keys are ignored
    by pypdf. With ``flatten=True`` the filled values are baked into the page
    and the fields stop being editable, which needs pypdf 5.0 or newer.

    Returns the output path.
    """
    values = _load_data(data)
    writer = PdfWriter(clone_from=input_path)

    signature = inspect.signature(writer.update_page_form_field_values)
    supports_flatten = "flatten" in signature.parameters
    if flatten and not supports_flatten:
        raise PdfToolsError(
            "Flattening needs pypdf>=5.0. Upgrade pypdf, or fill without --flatten."
        )

    for page in writer.pages:
        kwargs: Dict[str, Any] = {"auto_regenerate": False}
        if supports_flatten:
            kwargs["flatten"] = flatten
        writer.update_page_form_field_values(page, values, **kwargs)

    if flatten:
        # pypdf's flatten bakes the value into page content but leaves the
        # interactive field definitions in place. Drop them so the result is a
        # genuinely static document: no editable fields survive.
        _strip_interactive_form(writer)
    else:
        # Ask viewers to regenerate field appearances so values show up.
        try:
            writer.set_need_appearances_writer(True)
        except Exception:  # pragma: no cover - older pypdf naming
            pass

    ensure_parent_dir(output)
    with open(output, "wb") as fh:
        writer.write(fh)
    return output
