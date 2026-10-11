"""Original synthetic ZIP/XML inputs, no private documents or external services."""
import io
from pathlib import Path
import sys
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "人物蒸馏" / "scripts"))
import local_docx as docx


def package(body, extra=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="' + docx.W[1:-1] + '"><w:body>' + body + '</w:body></w:document>')
        for name, value in (extra or {}).items():
            archive.writestr(name, value)
    return stream.getvalue()


class LocalDocxTests(unittest.TestCase):
    def test_paragraphs_breaks_tables_unicode_and_insertions(self):
        body = '<w:p><w:r><w:t>原创 &amp; 文本</w:t><w:tab/><w:t>tail</w:t><w:br/><w:t>next</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:ins><w:p><w:r><w:t>inserted</w:t></w:r></w:p></w:ins>'
        self.assertEqual(docx.extract_docx(package(body)), "原创 & 文本 tail\nnext\ncell\ninserted")

    def test_deleted_text_fields_and_external_relationships_not_loaded(self):
        body = '<w:p><w:del><w:r><w:delText>deleted</w:delText><w:t>deleted</w:t></w:r></w:del><w:r><w:instrText>external field</w:instrText><w:t>Original result</w:t></w:r></w:p>'
        with patch("socket.socket", side_effect=AssertionError("network")):
            value = docx.extract_docx(package(body, {"word/_rels/document.xml.rels": '<Relationship Target="https://invalid.example"/>', "word/header1.xml": "private excluded header"}))
        self.assertEqual(value, "Original result")

    def test_invalid_zip_missing_body_and_empty_document(self):
        for value in (b"invalid", package(""), package("<w:p>")):
            with self.assertRaises(ValueError):
                docx.extract_docx(value)

    def test_dtd_entities_and_unsafe_member_rejected(self):
        with self.assertRaises(ValueError):
            docx.extract_docx(package('<!DOCTYPE p [<!ENTITY x "x">]><w:p/>'))
        with self.assertRaises(ValueError):
            docx.extract_docx(package('<w:p/>', {"../escape": "no"}))

    def test_input_expansion_node_and_depth_bounds(self):
        value = package('<w:p><w:r><w:t>original</w:t></w:r></w:p>')
        for name in ("MAX_INPUT_BYTES", "MAX_XML_BYTES", "MAX_TOTAL_BYTES", "MAX_NODES", "MAX_DEPTH"):
            with patch.object(docx, name, 1), self.assertRaises(ValueError):
                docx.extract_docx(value)


if __name__ == "__main__":
    unittest.main()
