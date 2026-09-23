"""AcroForm operations: inspect, fill, and flatten interactive forms.

Reading fields and filling values is handled by pypdf. On top of it, ``fill``
validates the data against the form before writing: checkbox and radio values
are mapped to the field's real "on" state (``true`` becomes ``/Yes``, or
whatever the form author named it), choice values are checked against the
field's options, and keys that match no field are reported instead of being
silently ignored. Flattening (baking the filled values in so they can no
longer be edited) needs a reasonably recent pypdf and is detected at runtime.
"""

from __future__ import annotations

import difflib
import inspect
import json
import os
import warnings
from typing import Any, Dict, List, Optional, Tuple, Union

from pypdf import PdfWriter
from pypdf.generic import NameObject

from ._util import (
    PdfToolsError,
    PdfToolsWarning,
    open_reader,
    open_writer,
    write_pdf,
)


_FIELD_TYPES = {
    "/Tx": "text",
    "/Btn": "button",
    "/Ch": "choice",
    "/Sig": "signature",
}

# Field flag bits (ISO 32000 Tables 226, 228, 230).
_FF_RADIO = 1 << 15
_FF_PUSHBUTTON = 1 << 16
_FF_EDIT = 1 << 18          # combo box accepts free text
_FF_MULTISELECT = 1 << 21   # list box allows several values

_TRUTHY = {"true", "yes", "on", "1", "checked", "x", "y"}
_FALSY = {"false", "no", "off", "0", "unchecked", "n", ""}


def _options(field) -> List[Tuple[str, str]]:
    """``(export, display)`` pairs for a choice field's ``/Opt`` array."""
    try:
        opt = field.get("/Opt")
    except Exception:
        opt = None
    pairs: List[Tuple[str, str]] = []
    for entry in opt or []:
        entry = entry.get_object() if hasattr(entry, "get_object") else entry
        if isinstance(entry, (list, tuple)) and entry:
            export = str(entry[0])
            display = str(entry[-1])
        else:
            export = display = str(entry)
        pairs.append((export, display))
    return pairs


def list_fields(input_path: str, *, password: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return every form field as a record.

    Each record has ``name``, ``type`` (text / button / choice / signature),
    ``value`` (current value or ``None``), ``options`` (display names for
    choice fields) and ``states`` (the on-states a checkbox or radio group
    accepts, e.g. ``["/Yes"]``). Returns an empty list for documents with no
    form.
    """
    reader = open_reader(input_path, password)
    fields = reader.get_fields() or {}
    records: List[Dict[str, Any]] = []
    for name, field in fields.items():
        raw_type = field.get("/FT")
        friendly = _FIELD_TYPES.get(raw_type, "unknown")
        value = field.get("/V")
        states: List[str] = []
        if raw_type == "/Btn":
            states = [str(s) for s in (field.get("/_States_") or []) if str(s) != "/Off"]
        records.append(
            {
                "name": str(name),
                "type": friendly,
                "value": None if value is None else str(value),
                "options": [display for _export, display in _options(field)],
                "states": states,
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
        try:
            with open(data, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
        except json.JSONDecodeError as exc:
            raise PdfToolsError(
                f"Form data {data!r} is not valid JSON (line {exc.lineno}: {exc.msg})."
            ) from exc
        if not isinstance(loaded, dict):
            raise PdfToolsError("Form data JSON must be an object of field:value.")
        return loaded
    raise PdfToolsError("data must be a dict or a path to a JSON file.")


def _coerce_button(name: str, field, value: Any) -> str:
    states = [str(s) for s in (field.get("/_States_") or [])]
    on_states = [s for s in states if s != "/Off"]
    is_radio = bool(int(field.get("/Ff", 0)) & _FF_RADIO)
    kind = "radio group" if is_radio else "checkbox"
    choices = ", ".join(on_states + ["/Off"])

    if isinstance(value, bool) or (
        isinstance(value, (int, float)) and value in (0, 1)
    ):
        truthy = bool(value)
    elif isinstance(value, str) and value.strip().lower() in _TRUTHY | _FALSY:
        # A state literally named like the value ("/Yes", "On") wins below.
        wanted = value.strip().lstrip("/").lower()
        for state in on_states:
            if state.lstrip("/").lower() == wanted:
                return state
        truthy = value.strip().lower() in _TRUTHY
    elif isinstance(value, str):
        wanted = value.strip().lstrip("/").lower()
        for state in on_states + ["/Off"]:
            if state.lstrip("/").lower() == wanted:
                return state
        raise ValueError(f"{value!r} is not a state of {kind} {name!r} (use {choices})")
    else:
        raise ValueError(f"{value!r} is not valid for {kind} {name!r} (use {choices})")

    if not truthy:
        return "/Off"
    if len(on_states) == 1:
        return on_states[0]
    if not on_states:
        raise ValueError(f"{kind} {name!r} has no on-state appearance to switch to")
    raise ValueError(
        f"{kind} {name!r} has several options; name one of {', '.join(on_states)}"
    )


def _coerce_choice(name: str, field, value: Any) -> Union[str, List[str]]:
    pairs = _options(field)
    flags = int(field.get("/Ff", 0))
    if isinstance(value, (list, tuple)):
        if not flags & _FF_MULTISELECT:
            raise ValueError(f"choice field {name!r} takes a single value, not a list")
        return [_coerce_choice(name, field, v) for v in value]  # type: ignore[misc]
    text = str(value)
    if not pairs or flags & _FF_EDIT:
        return text
    for export, display in pairs:
        if text == export:
            return export
    for export, display in pairs:
        if text == display or text.casefold() in (export.casefold(), display.casefold()):
            return export
    raise ValueError(
        f"{text!r} is not an option of {name!r} (options: "
        + ", ".join(display for _e, display in pairs) + ")"
    )


def _plan_values(
    writer: PdfWriter, values: Dict[str, Any]
) -> Tuple[Dict[str, Any], List[str]]:
    """Validate ``values`` against the form; return (clean values, problems)."""
    fields = writer.get_fields() or {}
    by_partial: Dict[str, str] = {}
    for qualified, field in fields.items():
        partial = field.get("/T")
        if partial is not None:
            by_partial.setdefault(str(partial), qualified)

    clean: Dict[str, Any] = {}
    problems: List[str] = []
    for key, value in values.items():
        if value is None:
            continue
        qualified = key if key in fields else by_partial.get(key)
        if qualified is None:
            hint = difflib.get_close_matches(key, list(fields), n=1)
            problems.append(
                f"no form field named {key!r}"
                + (f" (did you mean {hint[0]!r}?)" if hint else "")
            )
            continue
        field = fields[qualified]
        ftype = field.get("/FT")
        try:
            if ftype == "/Btn":
                if int(field.get("/Ff", 0)) & _FF_PUSHBUTTON:
                    raise ValueError(f"{key!r} is a push button and holds no value")
                clean[key] = _coerce_button(key, field, value)
            elif ftype == "/Ch":
                clean[key] = _coerce_choice(key, field, value)
            elif ftype == "/Sig":
                raise ValueError(f"{key!r} is a signature field and cannot be filled")
            else:
                clean[key] = value if isinstance(value, str) else (
                    json.dumps(value) if isinstance(value, bool) else str(value)
                )
        except ValueError as exc:
            problems.append(str(exc))
    return clean, problems


def fill(
    input_path: str,
    output: str,
    data: Union[Dict[str, Any], str],
    *,
    flatten: bool = False,
    strict: bool = False,
    password: Optional[str] = None,
) -> str:
    """Fill form fields from a dict or a JSON file.

    Keys are field names (fully qualified, or the field's own partial name;
    see :func:`list_fields`). Values are coerced to what the form expects:

    * text fields take any value (numbers are written as text);
    * checkboxes accept ``True``/``False`` and ``"yes"``/``"on"``/``"true"``
      (or ``"no"``/``"off"``/``"false"``) and are switched to the field's real
      on-state from its appearance dictionary, or ``/Off``;
    * radio groups take the export value of the button to select;
    * choice fields are checked against their options (editable combo boxes
      accept free text; multi-select lists accept a list).

    Keys that match no field, and values the field cannot take, are reported
    as :class:`~pdftools._util.PdfToolsWarning` (with a "did you mean"
    suggestion) and skipped; with ``strict=True`` they raise
    :class:`~pdftools._util.PdfToolsError` and nothing is written.

    With ``flatten=True`` the filled values are baked into the page and the
    fields stop being editable, which needs pypdf 5.0 or newer.

    Returns the output path.
    """
    values = _load_data(data)
    writer = open_writer(input_path, password)
    if "/AcroForm" not in writer._root_object:  # noqa: SLF001
        raise PdfToolsError(f"{input_path!r} has no interactive form to fill.")

    clean, problems = _plan_values(writer, values)
    if problems and strict:
        raise PdfToolsError(
            "Form data does not match the form:\n  - " + "\n  - ".join(problems)
        )
    for problem in problems:
        warnings.warn(f"{problem}; skipped", PdfToolsWarning, stacklevel=2)

    signature = inspect.signature(writer.update_page_form_field_values)
    supports_flatten = "flatten" in signature.parameters
    if flatten and not supports_flatten:
        raise PdfToolsError(
            "Flattening needs pypdf>=5.0. Upgrade pypdf, or fill without --flatten."
        )

    for page in writer.pages:
        if "/Annots" not in page:
            continue
        kwargs: Dict[str, Any] = {"auto_regenerate": False}
        if supports_flatten:
            kwargs["flatten"] = flatten
        writer.update_page_form_field_values(page, clean, **kwargs)

    # pypdf stores a button group's value as text; the spec wants a name, and
    # viewers use it to decide which radio button is on.
    fields = writer.get_fields() or {}
    for key, value in clean.items():
        field = fields.get(key)
        if field is None or field.get("/FT") != "/Btn" or not isinstance(value, str):
            continue
        ref = getattr(field, "indirect_reference", None)
        target = ref.get_object() if ref is not None else None
        if target is not None and "/V" in target:
            target[NameObject("/V")] = NameObject(value)

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

    return write_pdf(writer, output)
