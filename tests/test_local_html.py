"""Offline HTML extraction with original synthetic content only."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "人物蒸馏" / "scripts"))
import local_html


class LocalHtmlTests(unittest.TestCase):
    def test_blocks_entities_inline_and_table(self):
        value = local_html.extract_html(b"<h1>Original</h1><p>A <b>bold</b> &amp; B</p><table><tr><td>One</td><td>Two</td></tr></table>")
        self.assertEqual(value, "Original\nA bold & B\nOne\nTwo")

    def test_non_content_and_hidden_subtrees_omitted(self):
        value = local_html.extract_html(b'<head><title>Omit</title></head><script>bad()</script><style>bad</style><template>bad</template><div hidden>bad<b>bad</b></div><span aria-hidden="true">bad</span><!--bad--><p>Original</p>')
        self.assertEqual(value, "Original")

    def test_remote_attributes_are_neither_read_nor_fetched(self):
        with patch("socket.socket", side_effect=AssertionError("No networking")):
            self.assertEqual(local_html.extract_html(b'<img src="https://invalid.example/x"><iframe src="https://invalid.example">bad</iframe><a href="https://invalid.example">Original link</a>'), "Original link")

    def test_utf8_bom_and_self_closing_tags(self):
        self.assertEqual(local_html.extract_html(('\ufeff<p>原创<br/>文本</p><div hidden/>尾部').encode()), "原创\n文本\n尾部")

    def test_empty_invalid_encoding_nul_and_size_rejected(self):
        for value in (b"<script>bad</script>", b"\xff", b"a\x00b"):
            with self.assertRaises((ValueError, UnicodeError)):
                local_html.extract_html(value)
        with patch.object(local_html, "MAX_BYTES", 2), self.assertRaises(ValueError):
            local_html.extract_html(b"abc")

    def test_depth_and_event_limits(self):
        with patch.object(local_html, "MAX_DEPTH", 2), self.assertRaises(ValueError):
            local_html.extract_html(b"<div><div><div>text")
        with patch.object(local_html, "MAX_EVENTS", 2), self.assertRaises(ValueError):
            local_html.extract_html(b"<p>text</p>")


if __name__ == "__main__":
    unittest.main()
