"""Offline PDF text extraction in a time-limited local worker, with sanitized errors."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys

MAX_BYTES = 5 * 1024 * 1024
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_PAGES = 200
TIMEOUT = 30
ERRORS = {"pdf_dependency_missing", "pdf_dependency_version", "pdf_encrypted", "pdf_page_limit",
          "pdf_text_limit", "pdf_empty_text_or_scan", "pdf_invalid_or_unsupported"}


def _extract(payload):
    try:
        import pypdf
        import pypdf.filters
    except ImportError:
        return {"error": "pdf_dependency_missing"}
    try:
        version = tuple(int(v) for v in pypdf.__version__.split(".")[:3])
        if not (version >= (6, 14, 2) and version < (7,)):
            return {"error": "pdf_dependency_version"}
        pypdf.filters.ZLIB_MAX_OUTPUT_LENGTH = 10 * 1024 * 1024
        reader = pypdf.PdfReader(io.BytesIO(payload), strict=True)
        if reader.is_encrypted:
            return {"error": "pdf_encrypted"}
        if len(reader.pages) > MAX_PAGES:
            return {"error": "pdf_page_limit"}
        pages = []
        total = 0
        for page in reader.pages:
            text = page.extract_text(extraction_mode="plain") or ""
            total += len(text.encode("utf-8"))
            if total > MAX_TEXT_BYTES:
                return {"error": "pdf_text_limit"}
            pages.append(text)
        if not any(text.strip() for text in pages):
            return {"error": "pdf_empty_text_or_scan"}
        return {"text": "\n\n".join(pages)}
    except Exception:
        # Parser diagnostics may contain document data. Never forward them.
        return {"error": "pdf_invalid_or_unsupported"}


def extract_pdf(payload: bytes) -> str:
    if not isinstance(payload, bytes) or len(payload) > MAX_BYTES:
        raise ValueError("pdf_input_limit")
    if not payload.startswith(b"%PDF-"):
        raise ValueError("pdf_invalid_header")
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker"],
            input=payload, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=TIMEOUT,
            check=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except subprocess.TimeoutExpired:
        raise ValueError("pdf_timeout") from None
    if result.returncode or len(result.stdout) > MAX_TEXT_BYTES * 6 + 4096:
        raise ValueError("pdf_worker_failed")
    try:
        value = json.loads(result.stdout)
    except (ValueError, UnicodeError):
        raise ValueError("pdf_worker_failed") from None
    if isinstance(value, dict) and set(value) == {"error"} and value["error"] in ERRORS:
        raise ValueError(value["error"])
    if (not isinstance(value, dict) or set(value) != {"text"} or not isinstance(value["text"], str)
            or not value["text"].strip() or len(value["text"].encode("utf-8")) > MAX_TEXT_BYTES + 2 * MAX_PAGES):
        raise ValueError("pdf_worker_failed")
    return value["text"]


if __name__ == "__main__":
    if sys.argv[1:] != ["--worker"]:
        sys.exit(2)
    data = sys.stdin.buffer.read(MAX_BYTES + 1)
    value = _extract(data) if len(data) <= MAX_BYTES else {"error": "pdf_invalid_or_unsupported"}
    sys.stdout.buffer.write(json.dumps(value, ensure_ascii=True).encode("ascii"))
