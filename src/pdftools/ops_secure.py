"""Encryption, decryption and privacy scrubbing.

pypdf handles the crypto; this module wraps it with clear password semantics,
real permission flags, and a metadata scrubber you can run before sharing a
file. Every operation clones the document, so forms, links and bookmarks
survive.
"""

from __future__ import annotations

import warnings
from typing import Dict, Iterable, List, Optional, Union

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject

from ._util import (
    PdfToolsError,
    PdfToolsWarning,
    open_reader,
    open_writer,
    write_pdf,
)


# ---------------------------------------------------------------------------
# Permissions (PDF 1.7 Table 22 / ISO 32000-2 Table 22)
# ---------------------------------------------------------------------------

#: Friendly permission names -> their bit in the /P entry.
PERMISSIONS: Dict[str, int] = {
    "print": 1 << 2,          # bit 3: print (low resolution unless print-hq)
    "modify": 1 << 3,         # bit 4: change page content
    "copy": 1 << 4,           # bit 5: copy / extract text and graphics
    "annotate": 1 << 5,       # bit 6: add or modify comments and form fields
    "fill-forms": 1 << 8,     # bit 9: fill in existing form fields
    "accessibility": 1 << 9,  # bit 10: extraction for screen readers
    "assemble": 1 << 10,      # bit 11: insert, rotate, delete pages, bookmarks
    "print-hq": 1 << 11,      # bit 12: full-quality printing
}

# Bits 7-8 and 13-32 must be 1 and bits 1-2 must be 0 (ISO 32000, 7.6.4.2).
_RESERVED_ON = 0xFFFFF0C0

#: What an "open freely, resist editing" file allows without the owner password.
DEFAULT_RESTRICTED = ("print", "print-hq", "accessibility")


def permissions_flag(permissions: Union[str, Iterable[str]]) -> int:
    """Turn permission names into a spec-correct unsigned 32-bit ``/P`` value.

    ``permissions`` is an iterable of names from :data:`PERMISSIONS`, a
    comma-separated string of them, ``"all"`` or ``"none"``.
    """
    if isinstance(permissions, str):
        names = [p for p in permissions.split(",")]
    else:
        names = list(permissions)
    flag = 0
    for raw in names:
        name = str(raw).strip().lower().replace("_", "-")
        if not name:
            continue
        if name == "all":
            for bit in PERMISSIONS.values():
                flag |= bit
        elif name == "none":
            continue
        elif name in PERMISSIONS:
            flag |= PERMISSIONS[name]
        else:
            raise PdfToolsError(
                f"Unknown permission {raw!r}. Use all, none or any of: "
                + ", ".join(PERMISSIONS)
            )
    return flag | _RESERVED_ON


def permissions_from_flag(flag: int) -> List[str]:
    """The permission names granted by a ``/P`` value (signed or unsigned)."""
    value = int(flag) & 0xFFFFFFFF
    return [name for name, bit in PERMISSIONS.items() if value & bit]


def encrypt(
    input_path: str,
    output: str,
    *,
    user_password: str,
    owner_password: Optional[str] = None,
    algorithm: str = "AES-256",
    permissions: Optional[Union[str, Iterable[str]]] = None,
    password: Optional[str] = None,
) -> str:
    """Encrypt a PDF with the standard security handler.

    Password semantics, which trip a lot of people up:

    * The **user password** is what a reader must type to *open* the document.
      Pass an empty string to leave it openable by anyone.
    * The **owner password** unlocks everything the permissions withhold. If
      you omit it, it defaults to the user password, and anyone who can open
      the file gets full rights.

    ``permissions`` lists what someone who opened the file with the *user*
    password may do: any of ``print``, ``print-hq``, ``modify``, ``copy``,
    ``annotate``, ``fill-forms``, ``accessibility``, ``assemble``, or
    ``"all"`` / ``"none"``. When it is omitted:

    * with an owner password that differs from the user password, the file
      allows ``print``, ``print-hq`` and ``accessibility`` only — it opens
      (and prints) freely but resists editing and copying;
    * otherwise every permission is granted.

    Permissions are honoured by compliant viewers; they are a policy flag, not
    cryptographic protection of the content once the file is open.

    ``algorithm`` is one of ``RC4-40``, ``RC4-128``, ``AES-128``, ``AES-256``.
    AES-256 is the sane default; the RC4 modes exist only for legacy readers.
    ``password`` opens the *input* if it is already encrypted.
    Returns the output path.
    """
    if algorithm not in ("RC4-40", "RC4-128", "AES-128", "AES-256"):
        raise PdfToolsError(
            "algorithm must be one of RC4-40, RC4-128, AES-128, AES-256."
        )
    separate_owner = bool(owner_password) and owner_password != user_password
    if permissions is None:
        permissions = DEFAULT_RESTRICTED if separate_owner else "all"
    flag = permissions_flag(permissions)
    restricted = permissions_from_flag(flag) != list(PERMISSIONS)
    if restricted and not separate_owner:
        warnings.warn(
            "Permissions are only enforced when an owner password different "
            "from the user password is set; anyone who opens this file can "
            "lift them.",
            PdfToolsWarning,
            stacklevel=2,
        )
    if not user_password and not owner_password:
        warnings.warn(
            "No user or owner password given: the file is encrypted but "
            "anyone can open it with full rights.",
            PdfToolsWarning,
            stacklevel=2,
        )

    writer = open_writer(input_path, password)
    writer.encrypt(
        user_password=user_password,
        owner_password=owner_password,
        algorithm=algorithm,
        permissions_flag=flag,
    )
    return write_pdf(writer, output)


def encryption_info(input_path: str, password: Optional[str] = None) -> Optional[Dict]:
    """Describe a file's encryption, or return ``None`` if it has none.

    Returns ``{"algorithm": "AES-256", "revision": 6, "permissions": [...]}``.
    """
    reader = open_reader(input_path, password)
    return _encryption_summary(reader)


def _encryption_summary(reader) -> Optional[Dict]:
    if not reader.is_encrypted:
        return None
    enc = reader.trailer["/Encrypt"].get_object()
    v = int(enc.get("/V", 0))
    r = int(enc.get("/R", 0))
    length = int(enc.get("/Length", 40))
    if v >= 5:
        algorithm = "AES-256"
    elif v == 4:
        cfm = None
        try:
            cfm = enc["/CF"][enc.get("/StmF", "/StdCF")]["/CFM"]
        except Exception:
            pass
        algorithm = "AES-128" if cfm == "/AESV2" else "RC4-128"
    elif v in (2, 3):
        algorithm = f"RC4-{length}"
    else:
        algorithm = "RC4-40"
    return {
        "algorithm": algorithm,
        "revision": r,
        "permissions": permissions_from_flag(int(enc.get("/P", -1))),
    }


def decrypt(input_path: str, output: str, *, password: str = "") -> str:
    """Remove encryption, producing an open copy.

    ``password`` may be either the user or the owner password. Raises a
    friendly error if the file is locked and the password does not match.
    Forms, bookmarks, links and metadata are kept.
    """
    reader = open_reader(input_path, password or None)
    writer = PdfWriter(clone_from=reader)
    return write_pdf(writer, output)


def strip_metadata(
    input_path: str,
    output: str,
    *,
    flatten_annotations: bool = False,
    password: Optional[str] = None,
) -> str:
    """Produce a privacy-scrubbed copy.

    Removes the document information dictionary (author, producer, creation
    software, timestamps) and every XMP ``/Metadata`` packet and
    ``/PieceInfo`` application-data dictionary — on the catalog, pages, images
    and fonts — and drops the orphaned objects so the data does not linger in
    the file. Forms, bookmarks and links are kept.

    With ``flatten_annotations=True`` it also removes annotation objects
    (comments, highlights, stamps, and form widgets) which frequently carry a
    reviewer's name; the interactive form goes with them.

    Returns the output path.
    """
    writer = open_writer(input_path, password)
    writer.metadata = None  # drop the /Info dictionary entirely

    for obj in list(writer._objects):  # noqa: SLF001 - stable pypdf internal
        if isinstance(obj, DictionaryObject):
            for key in ("/Metadata", "/PieceInfo"):
                if key in obj:
                    del obj[NameObject(key)]

    if flatten_annotations:
        try:
            writer.remove_annotations(subtypes=None)
        except Exception as exc:  # pragma: no cover - very old pypdf
            raise PdfToolsError(
                "This pypdf build cannot remove annotations; upgrade pypdf."
            ) from exc
        root = writer._root_object  # noqa: SLF001
        if "/AcroForm" in root:
            del root[NameObject("/AcroForm")]

    try:
        # Physically drop the now-unreferenced XMP streams and info dict.
        writer.compress_identical_objects()
    except Exception:  # pragma: no cover - older pypdf lacks this
        pass
    return write_pdf(writer, output)
