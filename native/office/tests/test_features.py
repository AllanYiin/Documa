from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile

import jsonschema
import pytest
import rust_office

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"


def package(parts):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        for name, value in parts.items():
            z.writestr(name, value)
    return out.getvalue()


def rels(items):
    return f'<Relationships xmlns="{REL}">' + "".join(
        f'<Relationship Id="{id}" Type="{OFFICE_REL}{kind}" Target="{target}"{extra}/>'
        for id, kind, target, extra in items
    ) + '</Relationships>'


def events(data, suffix, **options):
    return list(rust_office.open(data, source_name="sample." + suffix, **options))


def units(payload):
    return [e["unit"] for e in payload if e["event"] == "unit"]


def cells(payload):
    return {c["address"]: c for u in units(payload) for b in u["blocks"] for c in b["metadata"].get("cell_records", [])}


def docx(body, extra=None, relationships=None):
    parts = {"word/document.xml": '<w:document xmlns:w="urn:w" xmlns:r="urn:r" xmlns:a="urn:a" xmlns:wp="urn:wp"><w:body>' + body + '</w:body></w:document>'}
    if relationships:
        parts["word/_rels/document.xml.rels"] = rels(relationships)
    parts.update(extra or {})
    return package(parts)


def paragraph(text):
    return f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>'


def xlsx(rows, extra=None, sheet_extra="", sheet_part="xl/worksheets/custom.xml"):
    parts = {
        "xl/workbook.xml": '<workbook xmlns:r="urn:r"><sheets><sheet name="Data" sheetId="99" r:id="s"/></sheets><definedNames><definedName name="Prices">Data!$A$1:$B$2</definedName></definedNames></workbook>',
        "xl/_rels/workbook.xml.rels": rels([("s", "worksheet", "/" + sheet_part, "")]),
        sheet_part: '<worksheet><sheetData>' + rows + '</sheetData>' + sheet_extra + '</worksheet>',
    }
    parts.update(extra or {})
    return package(parts)


@pytest.mark.parametrize("option", [
    {"revision_mode": "unknown"}, {"formula_mode": "unknown"}, {"external_links": "fetch"},
    {"asset_mode": "unknown"}, {"typo": True}, {"limits": {"unknown": 3}},
    {"limits": {"max_cells": 0}}, {"limits": {"max_xml_depth": 257}},
    {"batch_rows": -1}, {"cell_range": "B2:A1"}, {"cell_range": "XFE1"},
])
def test_strict_options(option):
    with pytest.raises(rust_office.OfficeError) as error:
        events(docx(paragraph("test")), "docx", **option)
    assert error.value.code == "INVALID_OPTIONS"
    assert isinstance(error.value, ValueError)


def test_bytes_stream_and_lazy_serialization():
    path = FIXTURES / "smoke.docx"
    expected = list(rust_office.open(path))
    assert list(rust_office.open(path.read_bytes(), source_name=str(path))) == expected
    assert list(rust_office.open(io.BytesIO(path.read_bytes()), source_name=str(path))) == expected
    stream = rust_office.open(path)
    count = stream.remaining()
    assert count == len(expected)
    assert next(stream) == stream.metadata
    assert stream.remaining() == count - 1
    list(stream)
    assert stream.remaining() == 0
    with pytest.raises(rust_office.OfficeError, match="INPUT_LIMIT_EXCEEDED"):
        rust_office.open(io.BytesIO(path.read_bytes()), source_name="x.docx", limits={"max_input_bytes": 5})
    with pytest.raises(rust_office.OfficeError, match="OFFICE_OPEN_FAILED"):
        rust_office.detect_format(ROOT / "does-not-exist.docx")


def test_word_revisions_entities_and_styles():
    data = docx('<w:p><w:r><w:t>A &amp; B</w:t></w:r><w:del><w:r><w:delText>old</w:delText></w:r></w:del><w:ins><w:r><w:t>new</w:t></w:r></w:ins><w:r><w:rPr><w:b w:val="0"/></w:rPr><w:t>plain</w:t></w:r></w:p>')
    text = lambda mode: units(events(data, "docx", revision_mode=mode))[0]["blocks"][0]
    assert text("final")["text"] == "A & Bnewplain"
    assert text("original")["text"] == "A & Boldplain"
    assert text("all")["text"] == "A & Boldnewplain"
    assert text("all")["spans"][-1]["styles"] == []


def test_word_merged_nested_and_annotations():
    table = '<w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge w:val="restart"/></w:tcPr>' + paragraph("anchor") + '</w:tc></w:tr><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge/></w:tcPr></w:tc></w:tr><w:tr><w:tc>' + paragraph("outer") + '<w:tbl><w:tr><w:tc>' + paragraph("nested") + '</w:tc></w:tr></w:tbl></w:tc></w:tr></w:tbl>'
    data = docx('<w:p><w:r><w:t>body</w:t><w:footnoteReference w:id="2"/></w:r><w:hyperlink r:id="link"><w:r><w:t>site</w:t></w:r></w:hyperlink></w:p>' + table,
        {"word/notes.xml": '<w:footnotes xmlns:w="urn:w"><w:footnote w:id="2">' + paragraph("note") + '</w:footnote></w:footnotes>'},
        [("link", "hyperlink", "https://example.org/a", ' TargetMode="External"'), ("n", "footnotes", "notes.xml", "")])
    unit = units(events(data, "docx"))[0]
    main = next(t for t in unit["tables"] if t["metadata"]["parent_table_id"] is None)
    cell = main["metadata"]["cells"][0]
    assert (cell["rowspan"], cell["colspan"]) == (2, 2)
    assert main["rows"][0] == ["anchor", None]
    assert main["rows"][1] == [None, None]
    assert main["metadata"]["cells"][-1]["nested_table_ids"]
    assert unit["blocks"][0]["metadata"]["annotation_refs"] == ["footnote:2"]
    assert unit["blocks"][0]["metadata"]["hyperlinks"][0]["target"] == "https://example.org/a"
    assert any(b["metadata"].get("annotation_key") == "footnote:2" for b in unit["blocks"])
    assert not units(events(data, "docx", external_links="ignore"))[0]["blocks"][0]["metadata"]["hyperlinks"]


def test_word_image_links_and_metadata_only():
    data = docx('<w:p><w:r><w:drawing><wp:inline><wp:docPr descr="logo"/><a:blip r:embed="image"/></wp:inline></w:drawing></w:r></w:p>',
        {"word/media/pic.png": b"example"}, [("image", "image", "media/pic.png", "")])
    payload = events(data, "docx", asset_mode="metadata")
    asset = next(e["asset"] for e in payload if e["event"] == "asset")
    assert asset["data_base64"] == ""
    assert asset["sha256"] == hashlib.sha256(b"example").hexdigest()
    assert asset["alt_text"] == "logo"
    assert asset["id"] in units(payload)[0]["blocks"][0]["metadata"]["asset_ids"]


def rewrite_pptx(change):
    with zipfile.ZipFile(FIXTURES / "pptx-multislide.pptx") as z:
        parts = {n: z.read(n) for n in z.namelist() if n != "[Content_Types].xml"}
    change(parts)
    return package(parts)


def test_pptx_relationship_order_hidden_and_notes():
    original = units(events((FIXTURES / "pptx-multislide.pptx").read_bytes(), "pptx"))
    def change(parts):
        xml = parts["ppt/presentation.xml"]
        found = re.search(rb'<p:sldIdLst>(.*?)</p:sldIdLst>', xml, re.S)
        ids = re.findall(rb'<p:sldId\b[^>]*/>', found[1])
        parts["ppt/presentation.xml"] = xml[:found.start(1)] + b"".join(reversed(ids)) + xml[found.end(1):]
        parts["ppt/slides/slide1.xml"] = re.sub(rb'<p:sld\b', b'<p:sld show="0"', parts["ppt/slides/slide1.xml"], count=1)
    data = rewrite_pptx(change)
    visible = units(events(data, "pptx"))
    expanded = units(events(data, "pptx", include_hidden=True))
    texts = lambda u: [b["text"] for b in u["blocks"] if b["kind"] == "heading"]
    assert len(visible) == 2 and len(expanded) == 3
    assert texts(expanded[0]) == texts(original[-1])
    assert expanded[-1]["hidden"] is True
    with zipfile.ZipFile(FIXTURES / "pptx-table-notes.pptx") as z:
        parts = {n: z.read(n) for n in z.namelist() if n != "[Content_Types].xml"}
    old = "ppt/notesSlides/notesSlide1.xml"
    parts["ppt/notesSlides/custom.xml"] = parts.pop(old)
    for n in parts:
        if n.endswith(".rels"):
            parts[n] = parts[n].replace(b"notesSlide1.xml", b"custom.xml")
    unit = units(events(package(parts), "pptx"))[0]
    assert any(b["metadata"].get("source_type") == "speaker_notes" for b in unit["blocks"])


def test_xlsx_sparse_batches_selection_metadata():
    rows = '<row r="1"><c r="A1" t="inlineStr"><is><t>heading</t></is></c><c r="B1"><v>3</v></c></row><row r="2" hidden="1"><c r="A2"><v>7</v></c></row><row r="1000000"><c r="XFD1000000"><v>42</v></c></row>'
    data = xlsx(rows, sheet_extra='<mergeCells><mergeCell ref="A1:B1"/></mergeCells>')
    result = events(data, "xlsx", sparse=True, batch_rows=100)
    unit = units(result)[0]
    assert len(unit["blocks"]) == 2
    assert set(cells(result)) == {"A1", "B1", "XFD1000000"}
    assert all(t["rows"] == [] for t in unit["tables"])
    assert unit["blocks"][0]["metadata"]["merged_ranges"] == ["A1:B1"]
    assert cells(result)["XFD1000000"]["raw_value"] == 42
    assert set(cells(events(data, "xlsx", cell_range="A1:B2", include_hidden=True))) == {"A1", "B1", "A2"}
    with pytest.raises(rust_office.OfficeError, match="SHEET_NOT_FOUND"):
        events(data, "xlsx", sheets=["unknown"])
    with pytest.raises(rust_office.OfficeError, match="PARSE_LIMIT_EXCEEDED"):
        events(data, "xlsx")


def test_xlsx_types_formats_formulas_and_tables():
    rows = '<row r="1"><c r="A1" s="1"><v>0.125</v></c><c r="B1" s="2"><v>45292</v></c><c r="C1"><f>A1*2</f><v>0.25</v></c><c r="D1"><f>A1*3</f><v/></c><c r="E1" t="b"><v>1</v></c></row>'
    extra = {
        "xl/styles.xml": '<styleSheet><numFmts><numFmt numFmtId="164" formatCode="yyyy-mm-dd"/></numFmts><cellXfs><xf numFmtId="0"/><xf numFmtId="10"/><xf numFmtId="164"/></cellXfs></styleSheet>',
        "xl/worksheets/_rels/custom.xml.rels": rels([("table", "table", "../tables/custom.xml", ""), ("link", "hyperlink", "https://example.org", ' TargetMode="External"')]),
        "xl/tables/custom.xml": '<table name="Sales" displayName="Sales" ref="A1:E1"><tableColumns><tableColumn id="1" name="Revenue"/></tableColumns></table>',
    }
    data = xlsx(rows, extra, '<hyperlinks><hyperlink ref="A1" xmlns:r="urn:r" r:id="link"/></hyperlinks>')
    payload = events(data, "xlsx")
    records = cells(payload)
    assert records["A1"]["raw_value"] == 0.125
    assert records["A1"]["display_text"] == "12.50%"
    assert records["B1"]["data_type"] == "datetime"
    assert records["B1"]["date_iso"].startswith("2024-01-01")
    assert records["C1"]["cache_status"] == "present_unverified"
    assert records["D1"]["cache_status"] == "missing"
    assert records["E1"]["raw_value"] is True
    assert payload[0]["warnings"][0]["code"] == "FORMULA_CACHE_MISSING"
    assert units(payload)[0]["metadata"]["tables"][0]["name"] == "Sales"
    assert payload[0]["metadata"]["defined_names"][0]["name"] == "Prices"
    assert cells(events(data, "xlsx", formula_mode="formula"))["C1"]["value"] == "A1*2"
    assert cells(events(data, "xlsx", formula_mode="cached_value"))["C1"]["formula"] is None


def test_shared_formulas_and_quoted_literals():
    data = xlsx('<row r="1"><c r="A1"><f t="shared" si="0">B1+$C$1+LEN("D1")+SUM(Table1[Col1])</f><v>1</v></c></row><row r="2"><c r="A2"><f t="shared" si="0"/><v>2</v></c></row>')
    assert cells(events(data, "xlsx"))["A2"]["formula"] == 'B2+$C$1+LEN("D1")+SUM(Table1[Col1])'


@pytest.mark.parametrize("suffix,name,limits", [
    ("docx", "smoke.docx", {"max_text_chars": 3}),
    ("pptx", "pptx-multislide.pptx", {"max_shapes": 1}),
    ("xlsx", "smoke.xlsx", {"max_cells": 1}),
    ("xls", "smoke.xls", {"max_cells": 1}),
    ("docx", "smoke.docx", {"max_xml_depth": 1}),
])
def test_resource_limits_fail_before_events(suffix, name, limits):
    with pytest.raises(rust_office.OfficeError, match="PARSE_LIMIT_EXCEEDED"):
        rust_office.open(FIXTURES / name, limits=limits)


def test_xls_merged_and_range():
    result = list(rust_office.open(FIXTURES / "xls-merged.xls"))
    assert units(result)[0]["blocks"][0]["metadata"]["merged_ranges"]
    selected = list(rust_office.open(FIXTURES / "xls-formula.xls", cell_range="C2"))
    assert set(cells(selected)) == {"C2"}


def test_chart_and_smartart_data():
    with zipfile.ZipFile(FIXTURES / "pptx-multislide.pptx") as z:
        parts = {n: z.read(n) for n in z.namelist() if n != "[Content_Types].xml"}
    parts["ppt/slides/_rels/slide1.xml.rels"] = rels([("c", "chart", "../charts/chart99.xml", ""), ("s", "diagramData", "../diagrams/data99.xml", "")])
    parts["ppt/charts/chart99.xml"] = '<chartSpace><chart><title><p><r><t>Revenue</t></r></p></title><barChart><ser><tx><v>Sales</v></tx><cat><strRef><strCache><pt idx="0"><v>Q1</v></pt></strCache></strRef></cat><val><numRef><f>Sheet1!B1</f><numCache><pt idx="0"><v>123</v></pt></numCache></numRef></val></ser></barChart></chart></chartSpace>'
    parts["ppt/diagrams/data99.xml"] = '<dataModel><pt><p><r><t>Step one</t></r></p></pt></dataModel>'
    payload = events(package(parts), "pptx")
    graphics = units(payload)[0]["metadata"]["graphics"]
    chart = next(g for g in graphics if g["kind"] == "chart")
    assert chart["title"] == "Revenue" and chart["series"][0]["values"][0]["value"] == "123"
    assert next(g for g in graphics if g["kind"] == "smartart")["text"] == "Step one"


def test_schema_validates_all_nested_events():
    validator = jsonschema.Draft202012Validator(rust_office.schema())
    for suffix in ("docx", "xls", "xlsx"):
        for event in rust_office.open(FIXTURES / f"smoke.{suffix}"):
            validator.validate(event)
    payload = list(rust_office.open(FIXTURES / "pptx-table-notes.pptx"))
    for event in payload:
        validator.validate(event)
    broken = copy.deepcopy(next(e for e in payload if e["event"] == "unit"))
    broken["unit"]["blocks"][0]["bbox"] = [1, 2]
    assert not validator.is_valid(broken)


def cli():
    configured = os.environ.get("OFFICE_CLI")
    path = Path(configured) if configured else ROOT / "target" / "debug" / ("office-cli.exe" if os.name == "nt" else "office-cli")
    if not path.is_file():
        pytest.skip("Build office-cli or set OFFICE_CLI for CLI integration tests")
    return str(path)


def test_cli_batch_and_exports(tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "sample.xlsx").write_bytes((FIXTURES / "smoke.xlsx").read_bytes())
    (source / "broken.docx").write_bytes(b"bad")
    output = tmp_path / "batch"
    result = subprocess.run([cli(), "batch", str(source), "--output-dir", str(output), "--format", "csv"], capture_output=True)
    assert result.returncode == 2
    report = json.loads((output / "batch-report.json").read_text())
    assert report["succeeded"] == 1 and report["failed"] == 1
    assert (output / "sample.xlsx.csv").read_text().startswith("unit,table,row,column,value")
    path = tmp_path / "result.md"
    subprocess.run([cli(), "parse", str(FIXTURES / "smoke.docx"), "--format", "markdown", "--output", str(path)], check=True)
    assert "繁體中文" in path.read_text(encoding="utf-8")
    assets = tmp_path / "assets"
    subprocess.run([cli(), "parse", str(FIXTURES / "pptx-image-link.pptx"), "--assets-dir", str(assets), "--output", str(tmp_path / "out.jsonl")], check=True)
    manifest = json.loads((assets / "manifest.json").read_text())
    assert manifest and hashlib.sha256((assets / manifest[0]["file"]).read_bytes()).hexdigest() == manifest[0]["sha256"]


@pytest.mark.parametrize("malformed", [b"", b"<worksheet>", b"<wrong/>", b"<worksheet/><worksheet/>"])
def test_malformed_worksheet_rejected(malformed):
    data = xlsx('', {"xl/worksheets/custom.xml": malformed})
    with pytest.raises(rust_office.OfficeError):
        events(data, "xlsx")


def test_hidden_columns_and_date1904():
    data = xlsx('<row r="1"><c r="A1"><v>3</v></c><c r="B1"><v>4</v></c></row>', sheet_extra='<cols><col min="2" max="2" hidden="1"/></cols>')
    assert set(cells(events(data, "xlsx"))) == {"A1"}
    assert set(cells(events(data, "xlsx", include_hidden=True))) == {"A1", "B1"}
    data = xlsx('<row r="1"><c r="A1" s="1"><v>1</v></c></row>', {
        "xl/workbook.xml": '<workbook xmlns:r="urn:r"><workbookPr date1904="1"/><sheets><sheet name="Data" r:id="s"/></sheets></workbook>',
        "xl/styles.xml": '<styleSheet><cellXfs><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>',
    })
    value = cells(events(data, "xlsx"))["A1"]
    assert value["date_iso"].startswith("1904-01-02") and value["date_system"] == "1904"


def test_shared_string_text_budget_and_bad_stream_options():
    data = xlsx('<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>0</v></c></row>', {"xl/sharedStrings.xml": '<sst><si><t>123456</t></si></sst>'})
    with pytest.raises(rust_office.OfficeError, match="PARSE_LIMIT_EXCEEDED"):
        events(data, "xlsx", limits={"max_text_chars": 8})
    with pytest.raises(rust_office.OfficeError, match="INVALID_OPTIONS"):
        rust_office.open(io.BytesIO(b""), source_name="x.docx", limits=[])


def test_relationship_traversal_and_duplicate_ids():
    with pytest.raises(rust_office.OfficeError, match="ZIP_PATH_TRAVERSAL"):
        events(docx(paragraph("text"), {}, [("h", "header", "../../outside.xml", "")]), "docx")
    with pytest.raises(rust_office.OfficeError, match="INVALID_RELATIONSHIP"):
        events(docx(paragraph("text"), {}, [("h", "header", "a.xml", ""), ("h", "header", "b.xml", "")]), "docx")


def test_xls_number_formats_and_visibility(tmp_path):
    import xlwt
    book = xlwt.Workbook()
    sheet = book.add_sheet("Data")
    sheet.write(0, 0, 0.125, xlwt.easyxf(num_format_str="0.00%"))
    sheet.write(0, 1, "column hidden")
    sheet.col(1).hidden = True
    sheet.write(1, 0, "row hidden")
    sheet.row(1).hidden = True
    path = tmp_path / "styled.xls"
    book.save(str(path))
    record = cells(list(rust_office.open(path)))["A1"]
    assert record["raw_value"] == 0.125 and record["number_format"] == "0.00%"
    assert record["display_text"] == "12.50%"
    assert set(cells(list(rust_office.open(path)))) == {"A1"}
    assert set(cells(list(rust_office.open(path, include_hidden=True)))) == {"A1", "B1", "A2"}


def test_pptx_table_span_and_shape_asset():
    with zipfile.ZipFile(FIXTURES / "pptx-table-notes.pptx") as z:
        parts = {n: z.read(n) for n in z.namelist() if n != "[Content_Types].xml"}
    xml = parts["ppt/slides/slide1.xml"]
    matches = list(re.finditer(rb'<a:tc>', xml))
    assert len(matches) >= 2
    # Replace from the right so offsets remain valid.
    xml = xml[:matches[1].start()] + b'<a:tc hMerge="1">' + xml[matches[1].end():]
    xml = xml[:matches[0].start()] + b'<a:tc gridSpan="2">' + xml[matches[0].end():]
    parts["ppt/slides/slide1.xml"] = xml
    table = units(events(package(parts), "pptx"))[0]["tables"][0]
    assert table["metadata"]["cells"][0]["colspan"] == 2
    assert table["rows"][0][1] is None
    payload = list(rust_office.open(FIXTURES / "pptx-image-link.pptx"))
    image = next(b for b in units(payload)[0]["blocks"] if b["kind"] == "image")
    ids = {e["asset"]["id"] for e in payload if e["event"] == "asset"}
    assert set(image["metadata"]["asset_ids"]) <= ids
    assert image["metadata"]["asset_ids"]


def test_unknown_word_content_warns():
    payload = events(docx('<w:altChunk r:id="uninterpreted"/>' + paragraph("text")), "docx")
    assert payload[0]["warnings"][0]["code"] == "UNSUPPORTED_WORD_STRUCTURE"


def test_corpus_gate_is_explicit(tmp_path):
    env = {**os.environ, "DOCUMA_OFFICE_CORPUS_DIR": str(tmp_path / "empty-corpus")}
    result = subprocess.run([os.sys.executable, str(ROOT / "scripts" / "corpus.py"), "check", "--require-real-world"], capture_output=True, text=True, env=env)
    assert result.returncode == 1 and "No authorized real-world" in result.stdout


@pytest.mark.parametrize("value", ["[]", '{"limits":[]}', '{"limits":{"max_cells":0}}'])
def test_cli_strict_json_options(value):
    result = subprocess.run([cli(), "parse", str(FIXTURES / "smoke.docx"), "--options-json", value], capture_output=True, text=True)
    assert result.returncode == 2 and "INVALID_OPTIONS" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(("suffix", "part", "xml"), [
    ("docx", "word/document.xml", "<wrong/>"),
    ("docx", "word/document.xml", "<document/>"),
    ("xlsx", "xl/workbook.xml", "<wrong><sheets/></wrong>"),
    ("pptx", "ppt/presentation.xml", "<wrong/>"),
])
def test_required_office_roots(suffix, part, xml):
    with pytest.raises(rust_office.OfficeError, match="OOXML_.*_INVALID"):
        events(package({part: xml}), suffix)


def test_renamed_macro_content_is_rejected():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("[Content_Types].xml", '<Types><Override ContentType="application/vnd.ms-word.document.macroEnabled.main+xml"/></Types>')
        z.writestr("word/document.xml", "<document><body/></document>")
    with pytest.raises(rust_office.OfficeError, match="MACRO_ENABLED_OFFICE_NOT_SUPPORTED"):
        events(out.getvalue(), "docx")


def test_local_corpus_workflow_uses_opt_in_directory(tmp_path):
    source = tmp_path / "synthetic.docx"
    source.write_bytes(docx(paragraph("Synthetic corpus regression")))
    permission = tmp_path / "permission.txt"
    permission.write_text("MIT: project-generated synthetic test fixture", encoding="utf-8")
    corpus = tmp_path / "private-corpus"
    env = {**os.environ, "DOCUMA_OFFICE_CORPUS_DIR": str(corpus)}
    command = [os.sys.executable, str(ROOT / "scripts" / "corpus.py")]
    subprocess.run(command + ["add", str(source), "--license", "MIT", "--source", "project-generated", "--permission-file", str(permission), "--reviewed"], env=env, check=True, capture_output=True)
    manifest_path = corpus / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(manifest["fixtures"]) == 1
    case = manifest["fixtures"][0]
    assert case["golden_reviewed"] is False
    golden = json.loads((corpus / case["golden"]).read_text(encoding="utf-8"))
    assert golden[0]["blocks"][0]["text"] == "Synthetic corpus regression"
    failed = subprocess.run(command + ["check", "--require-real-world"], env=env, capture_output=True)
    assert failed.returncode == 1
    case["golden_reviewed"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    subprocess.run(command + ["check", "--require-real-world"], env=env, check=True, capture_output=True)


def test_no_private_corpus_reports_skipped_in_public_ci(tmp_path):
    env = {**os.environ, "DOCUMA_OFFICE_CORPUS_DIR": str(tmp_path / "empty")}
    report = tmp_path / "report.json"
    command = [os.sys.executable, str(ROOT / "scripts" / "verify_real_world.py"), "--out", str(report)]
    subprocess.run(command, env=env, check=True, capture_output=True)
    assert json.loads(report.read_text())["status"] == "SKIPPED"
    required = subprocess.run(command + ["--require-real-world"], env=env, capture_output=True)
    assert required.returncode == 1
