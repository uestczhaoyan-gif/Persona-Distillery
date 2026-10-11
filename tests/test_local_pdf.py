"""PDF fixtures are generated in memory from original synthetic text only."""
import io
import builtins
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "人物蒸馏" / "scripts"))
import local_pdf as pdf
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject


def document(text=True, encrypted=False):
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    if text:
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 20 200 Td (Original synthetic PDF observation.) Tj ET")
        page[NameObject("/Contents")] = stream
    if encrypted:
        writer.encrypt("synthetic-password")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class LocalPdfTests(unittest.TestCase):
    def test_real_local_worker_extracts_original_text(self):
        self.assertIn("Original synthetic PDF observation.", pdf.extract_pdf(document()))

    def test_encrypted_and_image_only_or_blank_rejected(self):
        for data, message in ((document(encrypted=True), "pdf_encrypted"), (document(text=False), "pdf_empty_text_or_scan")):
            with self.assertRaisesRegex(ValueError, message):
                pdf.extract_pdf(data)

    def test_limits_invalid_and_timeout(self):
        with self.assertRaisesRegex(ValueError, "header"):
            pdf.extract_pdf(b"bad")
        with patch.object(pdf, "MAX_BYTES", 1), self.assertRaisesRegex(ValueError, "limit"):
            pdf.extract_pdf(b"%PDF-x")
        with patch.object(pdf.subprocess, "run", side_effect=subprocess.TimeoutExpired("worker", 1)):
            with self.assertRaisesRegex(ValueError, "pdf_timeout"):
                pdf.extract_pdf(document())
        with patch.object(pdf, "MAX_PAGES", 0):
            self.assertEqual(pdf._extract(document()), {"error": "pdf_page_limit"})
        with patch.object(pdf, "MAX_TEXT_BYTES", 1):
            self.assertEqual(pdf._extract(document()), {"error": "pdf_text_limit"})

    def test_worker_errors_are_sanitized(self):
        with patch.object(pdf.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"PRIVATE DIAGNOSTIC")):
            with self.assertRaisesRegex(ValueError, "^pdf_worker_failed$"):
                pdf.extract_pdf(document())
        self.assertEqual(pdf._extract(b"%PDF-bad"), {"error": "pdf_invalid_or_unsupported"})

    def test_missing_or_unsupported_dependency_explicit(self):
        original = builtins.__import__
        def missing(name, *args, **kwargs):
            if name == "pypdf":
                raise ImportError("Synthetic missing dependency")
            return original(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=missing):
            self.assertEqual(pdf._extract(b"%PDF-x"), {"error": "pdf_dependency_missing"})
        with patch("pypdf.__version__", "7.0.0"):
            self.assertEqual(pdf._extract(b"%PDF-x"), {"error": "pdf_dependency_version"})


if __name__ == "__main__":
    unittest.main()
