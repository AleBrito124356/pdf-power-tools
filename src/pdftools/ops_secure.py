"""Encryption, decryption and privacy scrubbing.

pypdf handles the crypto; this module wraps it with clear password semantics
and a metadata-stripping helper you can run before sharing a file.
"""

from __future__ import annotations

from typing import Optional

from pypdf import PdfReader, PdfWriter

from ._util import PdfToolsError, ensure_parent_dir


def encrypt(
    input_path: str,
    output: str,
    *,
    user_password: str,
    owner_password: Optional[str] = None,
    algorithm: str = "AES-256",
) -> str:
    """Encrypt a PDF with standard security handler.

    Password semantics, which trip a lot of people up:

    * The **user password** is what a reader must type to *open* the document.
      Pass an empty string to leave it openable by anyone while still applying
      owner restrictions.
    * The **owner password** unlocks full permissions (printing, copying,
      editing). If you omit it, it defaults to the user password, so there is
      no separate "master" key — set both explicitly when you want an open
      document that still resists editing.

    ``algorithm`` is one of ``RC4-40``, ``RC4-128``, ``AES-128``, ``AES-256``.
    AES-256 is the sane default; the RC4 modes exist only for legacy readers.
    Returns the output path.
    """
    if algorithm not in ("RC4-40", "RC4-128", "AES-128", "AES-256"):
        raise PdfToolsError(
            "algorithm must be one of RC4-40, RC4-128, AES-128, AES-256."
        )
    writer = PdfWriter(clone_from=input_path)
    writer.encrypt(
        user_password=user_password,
        owner_password=owner_password,
        algorithm=algorithm,
    )
    ensure_parent_dir(output)
    with open(output, "wb") as fh:
        writer.write(fh)
    return output


def decrypt(input_path: str, output: str, *, password: str = "") -> str:
    """Remove encryption, producing an open copy.

    ``password`` may be either the user or the owner password. Raises if the
    file is encrypted and the password does not match.
    """
    reader = PdfReader(input_path)
    if reader.is_encrypted:
        result = reader.decrypt(password)
        if int(result) == 0:
            raise PdfToolsError("Wrong password, or the file could not be decrypted.")

    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    if reader.metadata:
        writer.add_metadata(
            {k: v for k, v in reader.metadata.items() if v is not None}
        )
    ensure_parent_dir(output)
    with open(output, "wb") as fh:
        writer.write(fh)
    return output


def strip_metadata(
    input_path: str,
    output: str,
    *,
    flatten_annotations: bool = False,
) -> str:
    """Produce a privacy-scrubbed copy.

    Rebuilds the document without its information dictionary or XMP packet, so
    author, producer, creation software and timestamps do not travel with the
    file. With ``flatten_annotations=True`` it also removes annotation objects
    (comments, highlights, stamps) which frequently carry a reviewer's name —
    note this drops their interactivity, which is the point.

    Returns the output path.
    """
    reader = PdfReader(input_path)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    if flatten_annotations:
        try:
            writer.remove_annotations(subtypes=None)
        except Exception as exc:  # pragma: no cover - very old pypdf
            raise PdfToolsError(
                "This pypdf build cannot remove annotations; upgrade pypdf."
            ) from exc

    # Overwrite identifying producer/creator rather than leaving pypdf defaults.
    writer.add_metadata({"/Producer": "", "/Creator": ""})

    ensure_parent_dir(output)
    with open(output, "wb") as fh:
        writer.write(fh)
    return output
