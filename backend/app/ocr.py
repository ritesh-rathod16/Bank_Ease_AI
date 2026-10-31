"""Bounded, subprocess-isolated OCR calls backed by PyMuPDF's Tesseract support."""
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path


OCR_LANGUAGE = os.getenv("BANKEASE_OCR_LANGUAGE", "eng").strip()
OCR_DPI = int(os.getenv("BANKEASE_OCR_DPI", "150"))
OCR_MAX_PAGES = int(os.getenv("BANKEASE_OCR_MAX_PAGES", "10"))
OCR_PAGE_TIMEOUT_SECONDS = int(os.getenv("BANKEASE_OCR_PAGE_TIMEOUT_SECONDS", "15"))
OCR_DOCUMENT_TIMEOUT_SECONDS = int(os.getenv("BANKEASE_OCR_DOCUMENT_TIMEOUT_SECONDS", "45"))
MAX_OCR_OUTPUT_BYTES = 4_000_000
MAX_OCR_WORDS_PER_DOCUMENT = 50_000
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class OCRError(RuntimeError):
    pass


class OCREngineUnavailable(OCRError):
    pass


class OCRPageTimeout(OCRError):
    pass


@dataclass(frozen=True)
class OCRPage:
    text: str
    lines: list[dict]
    words: list[dict] = field(default_factory=list)


def validate_settings():
    if not re.fullmatch(r"[A-Za-z0-9_+-]{2,64}", OCR_LANGUAGE):
        raise OCRError("BANKEASE_OCR_LANGUAGE must contain a valid Tesseract language code.")
    if not 120 <= OCR_DPI <= 300:
        raise OCRError("BANKEASE_OCR_DPI must be between 120 and 300.")
    if not 1 <= OCR_MAX_PAGES <= 20:
        raise OCRError("BANKEASE_OCR_MAX_PAGES must be between 1 and 20.")
    if not 1 <= OCR_PAGE_TIMEOUT_SECONDS <= 30:
        raise OCRError("BANKEASE_OCR_PAGE_TIMEOUT_SECONDS must be between 1 and 30.")
    if not 5 <= OCR_DOCUMENT_TIMEOUT_SECONDS <= 120:
        raise OCRError("BANKEASE_OCR_DOCUMENT_TIMEOUT_SECONDS must be between 5 and 120.")


def extract_page(pdf_bytes: bytes, page_index: int, timeout_seconds=None) -> OCRPage:
    """Run OCR in a killable worker, passing the bounded upload only over stdin."""
    validate_settings()
    command = [sys.executable, "-m", "backend.app.ocr_worker", str(page_index), OCR_LANGUAGE, str(OCR_DPI)]
    try:
        result = subprocess.run(command, input=pdf_bytes, capture_output=True,
                                timeout=timeout_seconds or OCR_PAGE_TIMEOUT_SECONDS,
                                check=False, cwd=str(REPOSITORY_ROOT))
    except subprocess.TimeoutExpired as exc:
        raise OCRPageTimeout(f"OCR exceeded the {OCR_PAGE_TIMEOUT_SECONDS}-second page limit.") from exc
    except OSError as exc:
        raise OCREngineUnavailable("Could not start the OCR worker. Check the Python runtime and Tesseract installation.") from exc
    if len(result.stdout) > MAX_OCR_OUTPUT_BYTES:
        raise OCRError("OCR output exceeded the per-page text limit.")
    try:
        payload = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OCRError("OCR worker returned an invalid response.") from exc
    if not isinstance(payload, dict):
        raise OCRError("OCR worker returned an invalid response.")
    if result.returncode != 0 or payload.get("error"):
        code = payload.get("error_code")
        if code == "engine_unavailable":
            raise OCREngineUnavailable(
                "Scanned PDF detected, but Tesseract OCR is unavailable. Install Tesseract and its language data, then retry."
            )
        if code == "page_limit":
            raise OCRError("This scanned page exceeds the safe OCR image-dimension limit.")
        raise OCRError("OCR could not read this page. It will be reported as unreadable.")
    lines = payload.get("lines", [])
    words = payload.get("words", [])
    if not isinstance(lines, list) or not isinstance(words, list):
        raise OCRError("OCR worker returned malformed page geometry.")
    return OCRPage(
        text=str(payload.get("text", ""))[:500_000],
        lines=lines[:20_000],
        words=words[:30_000],
    )


def extract_pages(pdf_bytes: bytes, page_indexes: list[int]) -> dict[int, OCRPage]:
    """OCR selected pages serially with both per-page and per-document deadlines."""
    validate_settings()
    deadline = time.monotonic() + OCR_DOCUMENT_TIMEOUT_SECONDS
    results = {}
    word_count = 0
    for page_index in page_indexes:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        timeout = min(OCR_PAGE_TIMEOUT_SECONDS, max(1, int(remaining)))
        try:
            result = extract_page(pdf_bytes, page_index, timeout_seconds=timeout)
        except OCRPageTimeout:
            continue
        word_count += len(result.words)
        if word_count > MAX_OCR_WORDS_PER_DOCUMENT:
            raise OCRError("OCR document exceeded the safe word-count limit.")
        results[page_index] = result
    return results
