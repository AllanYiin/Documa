import base64
import builtins
from email.message import EmailMessage
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from documa.adapters import EmailAdapter, IpynbAdapter
from documa.adapters.base import ParseOptions
from documa.adapters.registry import adapter_for_source
from documa.core.errors import DocumaError


def _block_texts(document):
    return [block.text.raw_text for page in document.pages for block in page.blocks if block.text]


class EmailAndNotebookAdapterUnitTests(unittest.TestCase):
    def test_registry_routes_email_and_notebook_suffixes(self):
        self.assertIsInstance(adapter_for_source("sample.eml"), EmailAdapter)
        self.assertIsInstance(adapter_for_source("sample.msg"), EmailAdapter)
        self.assertIsInstance(adapter_for_source("sample.ipynb"), IpynbAdapter)

    def test_eml_html_body_is_converted_to_readable_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "html-only.eml"
            message = EmailMessage()
            message["Subject"] = "HTML only"
            message["From"] = "sender@example.com"
            message["To"] = "receiver@example.com"
            message.add_alternative("<html><body><h1>摘要</h1><p>請確認 HTML 內容。</p></body></html>", subtype="html")
            source.write_bytes(message.as_bytes())

            document = EmailAdapter().parse(source)

            self.assertEqual(document.parser, "eml")
            self.assertEqual(document.metadata["email"]["body_content_type"], "text/html")
            self.assertTrue(any("請確認 HTML 內容" in text for text in _block_texts(document)))

    def test_eml_attachment_asset_ref_is_sanitized(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "attachment.eml"
            asset_dir = tmp_path / "assets"
            message = EmailMessage()
            message["Subject"] = "附件"
            message["From"] = "sender@example.com"
            message["To"] = "receiver@example.com"
            message.set_content("附件檔名不可逃出 assets。")
            message.add_attachment(b"payload", maintype="text", subtype="plain", filename="../secret.txt")
            source.write_bytes(message.as_bytes())

            document = EmailAdapter().parse(source, ParseOptions(asset_dir=asset_dir))

            attachment = document.metadata["email"]["attachments"][0]
            self.assertNotIn("..", attachment["asset_ref"])
            self.assertTrue((asset_dir / attachment["asset_ref"]).exists())
            self.assertFalse((tmp_path / "secret.txt").exists())

    def test_msg_missing_dependency_returns_typed_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "mail.msg"
            source.write_bytes(b"msg")
            original_import = builtins.__import__

            def fake_import(name, *args, **kwargs):
                if name == "extract_msg":
                    raise ImportError("missing extract_msg")
                return original_import(name, *args, **kwargs)

            with mock.patch("builtins.__import__", side_effect=fake_import):
                with self.assertRaises(DocumaError) as caught:
                    EmailAdapter().parse(source)

            self.assertEqual(caught.exception.detail.code, "MSG_DEPENDENCY_NOT_INSTALLED")
            self.assertIn("pip install --upgrade documa", caught.exception.detail.suggested_action or "")

    def test_msg_open_failure_returns_typed_error_and_closes_message(self):
        class FailingMessage:
            closed = False

            @property
            def attachments(self):
                raise RuntimeError("broken msg")

            def close(self):
                self.closed = True

        message = FailingMessage()
        previous = sys.modules.get("extract_msg")
        sys.modules["extract_msg"] = types.SimpleNamespace(openMsg=lambda _: message)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp) / "broken.msg"
                source.write_bytes(b"msg")
                with self.assertRaises(DocumaError) as caught:
                    EmailAdapter().parse(source)
        finally:
            if previous is None:
                sys.modules.pop("extract_msg", None)
            else:
                sys.modules["extract_msg"] = previous

        self.assertEqual(caught.exception.detail.code, "MSG_OPEN_FAILED")
        self.assertTrue(message.closed)

    def test_msg_html_body_fallback_and_recipient_parsing(self):
        class FakeMessage:
            subject = "HTML MSG"
            senderEmail = "sender@example.com"
            to = "Alpha <alpha@example.com>; beta@example.com"
            cc = ["Gamma <gamma@example.com>"]
            htmlBody = b"<div>HTML <b>body</b> fallback</div>"
            attachments = []

        document = EmailAdapter()._payload_from_msg(FakeMessage(), None)

        self.assertEqual(document.body_content_type, "text/html")
        self.assertEqual(document.receivers, ["Alpha <alpha@example.com>", "beta@example.com"])
        self.assertEqual(document.cc, ["Gamma <gamma@example.com>"])
        self.assertIn("HTML", document.body)

    def test_ipynb_missing_dependency_returns_typed_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "notebook.ipynb"
            source.write_text("{}", encoding="utf-8")
            original_import = builtins.__import__

            def fake_import(name, *args, **kwargs):
                if name == "nbformat":
                    raise ImportError("missing nbformat")
                return original_import(name, *args, **kwargs)

            with mock.patch("builtins.__import__", side_effect=fake_import):
                with self.assertRaises(DocumaError) as caught:
                    IpynbAdapter().parse(source)

            self.assertEqual(caught.exception.detail.code, "IPYNB_DEPENDENCY_NOT_INSTALLED")
            self.assertIn("pip install --upgrade documa", caught.exception.detail.suggested_action or "")

    def test_ipynb_open_failure_returns_typed_error(self):
        try:
            import nbformat  # noqa: F401
        except ImportError:
            self.skipTest("nbformat is required")

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "invalid.ipynb"
            source.write_text("{not-json", encoding="utf-8")

            with self.assertRaises(DocumaError) as caught:
                IpynbAdapter().parse(source)

            self.assertEqual(caught.exception.detail.code, "IPYNB_OPEN_FAILED")

    def test_ipynb_preserves_raw_cells_output_preview_and_list_encoded_attachments(self):
        try:
            import nbformat  # type: ignore
        except ImportError:
            self.skipTest("nbformat is required")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "notebook.ipynb"
            asset_dir = tmp_path / "assets"
            notebook = nbformat.v4.new_notebook()
            markdown_cell = nbformat.v4.new_markdown_cell("Not a heading\nbody")
            markdown_cell["attachments"] = {
                "../chart.png": {"image/png": [base64.b64encode(b"image").decode("ascii")]}
            }
            code_cell = nbformat.v4.new_code_cell(
                "print('done')",
                execution_count=3,
                outputs=[
                    nbformat.v4.new_output("display_data", data={"text/plain": ["result", "\n"]}),
                    nbformat.v4.new_output("stream", name="stdout", text=["done", "\n"]),
                ],
            )
            raw_cell = nbformat.v4.new_raw_cell("raw note")
            notebook.cells = [markdown_cell, code_cell, raw_cell, nbformat.v4.new_markdown_cell("")]
            with source.open("w", encoding="utf-8", newline="\n") as handle:
                nbformat.write(notebook, handle)

            document = IpynbAdapter().parse(source, ParseOptions(asset_dir=asset_dir))
            blocks = [block for page in document.pages for block in page.blocks]

            self.assertEqual(document.metadata["cell_count"], 4)
            self.assertEqual([block.metadata["cell_type"] for block in blocks], ["markdown", "code", "raw"])
            self.assertEqual(blocks[0].type.value, "paragraph")
            self.assertEqual(blocks[1].metadata["execution_count"], 3)
            self.assertEqual(blocks[1].metadata["outputs_preview"], ["result", "done"])
            attachment = document.metadata["attachments"][0]
            self.assertNotIn("..", attachment["asset_ref"])
            self.assertTrue((asset_dir / attachment["asset_ref"]).exists())


if __name__ == "__main__":
    unittest.main()
