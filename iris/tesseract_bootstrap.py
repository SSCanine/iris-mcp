"""Locate Tesseract binary: bundled vendor/ first, then PATH, then well-known install dirs."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytesseract

VENDOR_DIR = Path(__file__).parent.parent / "vendor" / "tesseract"
WELL_KNOWN = [
    Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
    Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
]


def locate_tesseract() -> Path | None:
    bundled = VENDOR_DIR / "tesseract.exe"
    if bundled.exists():
        return bundled
    on_path = shutil.which("tesseract")
    if on_path:
        return Path(on_path)
    for p in WELL_KNOWN:
        if p.exists():
            return p
    return None


def configure_tesseract() -> bool:
    binary = locate_tesseract()
    if binary is None:
        return False
    pytesseract.pytesseract.tesseract_cmd = str(binary)
    return True
