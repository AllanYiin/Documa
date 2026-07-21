import unittest

from documa.core.language import LanguageHint, detect_text_script
from documa.core.text_normalization import normalize_for_search, normalize_unicode


class LanguageAndNormalizationTests(unittest.TestCase):
    def test_language_hint_accepts_traditional_simplified_and_english(self):
        self.assertEqual(LanguageHint("zh-Hant").language, "zh-Hant")
        self.assertEqual(LanguageHint("zh-Hans").language, "zh-Hans")
        self.assertEqual(LanguageHint("zh-TW").language, "zh-TW")
        self.assertEqual(LanguageHint("zh-Hant-TW").language, "zh-Hant-TW")
        self.assertEqual(LanguageHint("en-US").language, "en-US")
        self.assertEqual(LanguageHint("en", script="Latin").script, "Latin")

    def test_language_hint_normalizes_unicode_locale_separators(self):
        self.assertEqual(LanguageHint("zh_TW").language, "zh-TW")
        self.assertEqual(LanguageHint("zh_Hant_TW").language, "zh-Hant-TW")

    def test_language_hint_rejects_malformed_language_tags(self):
        for value in ("", "zh__TW", "zh--TW", "zh Taiwan", "en_US.UTF-8"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "Unsupported language hint"):
                    LanguageHint(value)

    def test_detect_text_script_is_conservative(self):
        self.assertEqual(detect_text_script("繁體中文資料"), "Traditional")
        self.assertEqual(detect_text_script("简体中文资料"), "Simplified")
        self.assertEqual(detect_text_script("English text"), "Latin")
        self.assertEqual(detect_text_script("繁體 English"), "Mixed")

    def test_normalize_for_search_does_not_replace_formal_normalization(self):
        self.assertEqual(normalize_unicode("ＡＢＣ", "NFC"), "ＡＢＣ")
        self.assertEqual(normalize_for_search("ＡＢＣ"), "abc")

