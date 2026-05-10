import tempfile
import unittest
from pathlib import Path

from documa.core.encoding import decode_text_bytes, read_text, write_text
from documa.core.errors import EncodingDetectionError


class EncodingTests(unittest.TestCase):
    def test_decode_utf8_traditional_chinese(self):
        text, encoding = decode_text_bytes("繁體中文 English".encode("utf-8"))

        self.assertEqual(text, "繁體中文 English")
        self.assertIn(encoding, {"utf-8", "utf-8-sig"})

    def test_decode_big5(self):
        raw = "繁體中文".encode("big5")
        text, encoding = decode_text_bytes(raw)

        self.assertEqual(text, "繁體中文")
        self.assertIn(encoding, {"cp950", "big5"})

    def test_write_and_read_utf8_path_with_chinese_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "測試 文件.txt"
            write_text(path, "繁體中文\nEnglish")
            text, encoding = read_text(path)

        self.assertEqual(text, "繁體中文\nEnglish")
        self.assertIn(encoding, {"utf-8", "utf-8-sig"})

    def test_decode_failure_is_typed(self):
        with self.assertRaises(EncodingDetectionError):
            decode_text_bytes(b"\xff\xfe\x00\x81", encodings=("utf-8",))


if __name__ == "__main__":
    unittest.main()

