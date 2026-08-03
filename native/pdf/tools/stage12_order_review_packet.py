#!/usr/bin/env python3
"""Build a private Stage 7.3 human-order review packet with node overlays."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from stage12_baseline import digest_file, load_contract, verify_case


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "tests" / "fixtures" / "stage12" / "baseline-contract.json"
DEFAULT_QUALITY = ROOT / "target" / "stage12-stage7b-parser-text" / "report.json"
DEFAULT_WHEEL_DIR = ROOT / "target" / "stage7c-bbox-python-exact"
DEFAULT_OUTPUT = ROOT / "target" / "stage12-stage7c-order-review-private"
REVIEW_UI_TEMPLATE = ROOT / "tools" / "stage12_order_review_ui.html"
FURNITURE_ROLES = {"artifact", "header", "footer", "page_number"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus-dir", type=Path, default=os.getenv("RUST_PDF_STAGE12_CORPUS_DIR")
    )
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--quality-report", type=Path, default=DEFAULT_QUALITY)
    parser.add_argument("--rust-wheel-dir", type=Path, default=DEFAULT_WHEEL_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--minimum-pages", type=int, default=24)
    parser.add_argument("--scale", type=float, default=1.5)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def affine_point(matrix: dict[str, Any], x: float, y: float) -> tuple[float, float]:
    return (
        float(matrix["a"]) * x + float(matrix["c"]) * y + float(matrix["e"]),
        float(matrix["b"]) * x + float(matrix["d"]) * y + float(matrix["f"]),
    )


def bbox_values(value: Any) -> tuple[float, float, float, float] | None:
    if isinstance(value, dict) and all(
        key in value for key in ("x0", "y0", "x1", "y1")
    ):
        return (
            float(value["x0"]),
            float(value["y0"]),
            float(value["x1"]),
            float(value["y1"]),
        )
    if isinstance(value, list) and len(value) == 4:
        return tuple(float(item) for item in value)
    return None


def display_bbox(
    bbox: Any,
    matrix: dict[str, Any],
) -> tuple[float, float, float, float]:
    values = bbox_values(bbox)
    if values is None:
        raise ValueError("display bbox must contain x0/y0/x1/y1")
    x0, y0, x1, y1 = values
    points = [
        affine_point(matrix, x0, y0),
        affine_point(matrix, x1, y0),
        affine_point(matrix, x1, y1),
        affine_point(matrix, x0, y1),
    ]
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def clip_bbox_to_bounds(
    bbox: tuple[float, float, float, float],
    bounds: tuple[float, float, float, float],
) -> tuple[float, float, float, float] | None:
    clipped = (
        max(bbox[0], bounds[0]),
        max(bbox[1], bounds[1]),
        min(bbox[2], bounds[2]),
        min(bbox[3], bounds[3]),
    )
    if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
        return None
    return clipped


def page_features(page: dict[str, Any]) -> set[str]:
    nodes = page.get("semantic_nodes", [])
    roles = {str(node.get("role") or "") for node in nodes}
    features: set[str] = set()
    if any(node.get("tag") for node in nodes):
        features.add("tagged")
    if page.get("tables"):
        features.add("table")
    if "caption" in roles:
        features.add("caption")
    if roles & {"list", "list_item", "label", "list_body"}:
        features.add("list")
    if roles & FURNITURE_ROLES or any(bool(node.get("artifact")) for node in nodes):
        features.add("furniture")
    spans = [span for node in nodes for span in node.get("spans", [])]
    if any(int(span.get("rotation", 0)) != 0 for span in spans):
        features.add("rotated_text")
    if any(str(span.get("writing_mode")) != "horizontal" for span in spans):
        features.add("vertical_writing")
    orders = page.get("orders", {})
    if orders.get("source_order") != orders.get("inferred_order"):
        features.add("inferred_reorder")
    geometry = page.get("geometry", {})
    bounds = bbox_values(geometry.get("layout_bounds")) or (0.0, 0.0, 1.0, 1.0)
    width = max(1.0, bounds[2] - bounds[0])
    centers = sorted(
        (values[0] + values[2]) / 2.0
        for node in nodes
        if (values := bbox_values(node.get("bbox"))) is not None
    )
    if any(right - left >= width * 0.22 for left, right in zip(centers, centers[1:])):
        features.add("multi_column_candidate")
    if any(
        str(node.get("role")) == "heading"
        and (values := bbox_values(node.get("bbox"))) is not None
        and values[2] - values[0] >= width * 0.6
        for node in nodes
    ):
        features.add("spanning_heading_candidate")
    if any(
        (values := bbox_values(node.get("bbox"))) is not None
        and values[2] - values[0] <= width * 0.35
        and values[0] >= width * 0.58
        for node in nodes
    ):
        features.add("sidebar_candidate")
    return features


def quality_index(
    report: dict[str, Any],
) -> dict[tuple[str, int], dict[str, float | int]]:
    result = {}
    for case in report["cases"]:
        document_id = str(case["id"])
        for page in case["pages"]:
            result[(document_id, int(page["page_number"]))] = {
                "character_f1": float(page["character_multiset"]["f1"]),
                "bigram_f1": float(page["character_bigram"]["f1"]),
                "length_delta": int(page["non_whitespace_length_delta"]),
            }
    return result


def seed_pages(
    quality: dict[tuple[str, int], dict[str, float | int]],
    document_ids: list[str],
    minimum_pages: int,
) -> set[tuple[str, int]]:
    ranked = sorted(
        quality,
        key=lambda key: (
            float(quality[key]["bigram_f1"]),
            float(quality[key]["character_f1"]),
            key[0],
            key[1],
        ),
    )
    selected = set(ranked[: max(1, minimum_pages)])
    for document_id in document_ids:
        candidates = [key for key in ranked if key[0] == document_id]
        if candidates:
            selected.add(candidates[0])
    return selected


def load_rust_pdf(wheel_dir: Path) -> Any:
    sys.path.insert(0, str(wheel_dir))
    import rust_pdf

    return rust_pdf


def collect_pages(
    cases: list[tuple[dict[str, Any], Path]],
    rust_pdf: Any,
    quality: dict[tuple[str, int], dict[str, float | int]],
    seeds: set[tuple[str, int]],
) -> tuple[
    dict[tuple[str, int], dict[str, Any]], dict[str, tuple[float, tuple[str, int]]]
]:
    retained: dict[tuple[str, int], dict[str, Any]] = {}
    feature_best: dict[str, tuple[float, tuple[str, int]]] = {}
    for case_index, (case, path) in enumerate(cases, start=1):
        document_id = str(case["id"])
        print(f"[{case_index}/{len(cases)}] layout {document_id}", flush=True)
        stream = rust_pdf.extract_layout_stream(
            path.read_bytes(),
            normalize_unicode=True,
            quality=True,
            debug_glyphs=False,
            timings=False,
        )
        for page in stream:
            key = (document_id, int(page["page_number"]))
            page_score = float(quality[key]["bigram_f1"])
            features = page_features(page)
            if key in seeds:
                retained[key] = page
            for feature in features:
                previous = feature_best.get(feature)
                if previous is None or (page_score, key) < previous:
                    feature_best[feature] = (page_score, key)
                    retained[key] = page
    return retained, feature_best


def page_image_and_nodes(
    pdf_document: Any,
    page_layout: dict[str, Any],
    scale: float,
) -> tuple[Image.Image, list[dict[str, Any]]]:
    import fitz

    page_number = int(page_layout["page_number"])
    pdf_page = pdf_document.load_page(page_number - 1)
    pixmap = pdf_page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")
    geometry = page_layout["geometry"]
    display_bounds = bbox_values(geometry["display_bounds"])
    if display_bounds is None:
        raise ValueError("display bounds are missing")
    display_width = max(1.0, display_bounds[2] - display_bounds[0])
    display_height = max(1.0, display_bounds[3] - display_bounds[1])
    matrix = geometry["layout_to_display"]
    rendered_nodes = []
    for node in page_layout.get("semantic_nodes", []):
        bbox = node.get("bbox")
        if bbox_values(bbox) is None:
            continue
        display_box = clip_bbox_to_bounds(display_bbox(bbox, matrix), display_bounds)
        if display_box is None:
            continue
        dx0, dy0, dx1, dy1 = display_box
        rendered_nodes.append(
            {
                "id": str(node["id"]),
                "box": (
                    int((dx0 - display_bounds[0]) * image.width / display_width),
                    int((dy0 - display_bounds[1]) * image.height / display_height),
                    int((dx1 - display_bounds[0]) * image.width / display_width),
                    int((dy1 - display_bounds[1]) * image.height / display_height),
                ),
                "percent_box": (
                    100.0 * (dx0 - display_bounds[0]) / display_width,
                    100.0 * (dy0 - display_bounds[1]) / display_height,
                    100.0 * max(0.0, dx1 - dx0) / display_width,
                    100.0 * max(0.0, dy1 - dy0) / display_height,
                ),
            }
        )
    return image, rendered_nodes


def render_overlay(
    image: Image.Image,
    rendered_nodes: list[dict[str, Any]],
    output_path: Path,
    title: str,
) -> None:
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = ImageFont.load_default(size=14)
    banner_height = 24
    draw.rectangle((0, 0, image.width, banner_height), fill=(20, 20, 20, 230))
    draw.text((6, 4), title, fill=(255, 255, 255, 255), font=font)
    for node in rendered_nodes:
        draw.rectangle(node["box"], outline=(72, 86, 104, 210), width=2)
    Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB").save(
        output_path,
        format="PNG",
        optimize=True,
    )


def json_script_payload(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).replace(
        "<", "\\u003c"
    )


def render_review_ui(
    template: str,
    pages: list[dict[str, Any]],
    draft: dict[str, Any],
    packet_key: str,
    *,
    initial_profile: str,
    locked_profile: str,
) -> str:
    replacements = {
        "__PACKET_PAGES__": json_script_payload(pages),
        "__PACKET_MANIFEST__": json_script_payload(draft),
        "__PACKET_KEY__": json_script_payload(packet_key),
        "__INITIAL_PROFILE__": json_script_payload(initial_profile),
        "__LOCKED_PROFILE__": json_script_payload(locked_profile),
    }
    rendered = template
    for token, value in replacements.items():
        if rendered.count(token) != 1:
            raise ValueError(f"review UI template token count is not one: {token}")
        rendered = rendered.replace(token, value)
    if (
        "__PACKET_" in rendered
        or "__INITIAL_PROFILE__" in rendered
        or "__LOCKED_PROFILE__" in rendered
    ):
        raise ValueError("review UI template contains an unresolved token")
    return rendered


def write_interactive_review(
    output_dir: Path,
    pages: list[dict[str, Any]],
    draft: dict[str, Any],
    packet_key: str,
) -> None:
    template = REVIEW_UI_TEMPLATE.read_text(encoding="utf-8")
    profiles = [
        ("reviewer-a.html", "reviewer-a", "reviewer-a"),
        ("reviewer-b.html", "reviewer-b", "reviewer-b"),
        ("adjudicate.html", "adjudicated", "adjudicated"),
    ]
    for filename, initial_profile, locked_profile in profiles:
        rendered = render_review_ui(
            template,
            pages,
            draft,
            packet_key,
            initial_profile=initial_profile,
            locked_profile=locked_profile,
        )
        (output_dir / filename).write_text(rendered, encoding="utf-8")

    launcher = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Private Stage 7.3 review launcher</title>
<style>
:root { color-scheme: dark; font-family: Aptos, "Segoe UI Variable", "Segoe UI", sans-serif;
  --bg:#07111f; --surface:#0d1b2d; --text:#f7f4ec; --muted:#9eb0c5;
  --border:#29435f; --accent:#38d1ad; --shadow:0 18px 48px rgb(0 0 0 / 38%); }
* { box-sizing:border-box; }
body { min-height:100vh; margin:0; display:grid; place-items:center; padding:24px;
  background:radial-gradient(circle at 20% 0%,rgb(56 209 173 / 14%),transparent 28rem),var(--bg); color:var(--text); }
main { width:min(760px,100%); padding:clamp(24px,5vw,48px); border:1px solid var(--border);
  border-radius:18px; background:var(--surface); box-shadow:var(--shadow); }
p { color:var(--muted); line-height:1.6; }
.flow { display:grid; grid-template-columns:1fr auto 1fr auto 1fr; gap:12px; align-items:center; margin-top:28px; }
a { min-height:76px; display:grid; place-items:center; padding:14px; border:1px solid var(--border);
  border-radius:10px; color:var(--text); text-decoration:none; text-align:center; font-weight:750; }
a:hover, a:focus-visible { border-color:var(--accent); background:rgb(56 209 173 / 10%); outline:none; }
.arrow { color:var(--accent); }
.notice { margin-top:28px; padding:12px 14px; border-left:3px solid var(--accent); background:#10283a; font-size:13px; }
@media(max-width:650px){ .flow{grid-template-columns:1fr}.arrow{transform:rotate(90deg);text-align:center} }
</style>
</head>
<body>
<main>
<p>PRIVATE STAGE 7.3 PACKET</p>
<h1>Human reading-order review</h1>
<p>Reviewer A and Reviewer B must work from independent packet copies. Export each reviewer file before comparison. Only then import both files into adjudication.</p>
<div class="flow" aria-label="Three-step review flow">
  <a href="reviewer-a.html">1. Reviewer A<br><small>Independent labels</small></a>
  <span class="arrow" aria-hidden="true">&#8594;</span>
  <a href="reviewer-b.html">2. Reviewer B<br><small>Independent labels</small></a>
  <span class="arrow" aria-hidden="true">&#8594;</span>
  <a href="adjudicate.html">3. Adjudication<br><small>Merge and resolve</small></a>
</div>
<p class="notice">Rendered pages and review files are private. Do not commit or redistribute this directory.</p>
</main>
</body>
</html>
"""
    (output_dir / "review.html").write_text(launcher, encoding="utf-8")


def blank_labels() -> dict[str, list[Any]]:
    return {
        "blocks": [],
        "block_precedence_pairs": [],
    }


def write_packet(
    args: argparse.Namespace,
    contract: dict[str, Any],
    cases: list[tuple[dict[str, Any], Path]],
    selected: set[tuple[str, int]],
    retained: dict[tuple[str, int], dict[str, Any]],
) -> None:
    if args.output_dir.exists():
        raise FileExistsError(
            f"private review output already exists: {args.output_dir}"
        )
    args.output_dir.mkdir(parents=True)
    clean_pages = args.output_dir / "pages"
    overlays = args.output_dir / "overlays"
    clean_pages.mkdir()
    overlays.mkdir()
    index_pages = []
    interactive_pages = []
    draft_documents = []
    import fitz

    case_map = {str(case["id"]): (case, path) for case, path in cases}
    for document_id in sorted({key[0] for key in selected}):
        case, path = case_map[document_id]
        document_pages = []
        pdf = fitz.open(path)
        try:
            for _, page_number in sorted(
                key for key in selected if key[0] == document_id
            ):
                key = (document_id, page_number)
                page = retained[key]
                image_name = f"{document_id}-page-{page_number:04d}.png"
                image, rendered_nodes = page_image_and_nodes(
                    pdf,
                    page,
                    max(0.5, float(args.scale)),
                )
                image.save(clean_pages / image_name, format="PNG", optimize=True)
                render_overlay(
                    image,
                    rendered_nodes,
                    overlays / image_name,
                    f"{document_id} | page {page_number} | coordinate QA",
                )
                node_ids = [str(node["id"]) for node in rendered_nodes]
                document_pages.append(
                    {
                        "page_number": page_number,
                        "adjudication_reason_codes": [],
                        "node_ids": node_ids,
                        "reviews": [
                            {"reviewer_id": "reviewer-a", "labels": blank_labels()},
                            {"reviewer_id": "reviewer-b", "labels": blank_labels()},
                        ],
                        "adjudicated": blank_labels(),
                    }
                )
                index_pages.append(
                    {
                        "document_id": document_id,
                        "page_number": page_number,
                        "clean_file": f"pages/{image_name}",
                        "overlay_file": f"overlays/{image_name}",
                    }
                )
                interactive_pages.append(
                    {
                        "document_id": document_id,
                        "page_number": page_number,
                        "clean_file": f"pages/{image_name}",
                        "nodes": [
                            {
                                "id": node["id"],
                                "percent_box": node["percent_box"],
                            }
                            for node in rendered_nodes
                        ],
                    }
                )
        finally:
            pdf.close()
        draft_documents.append(
            {
                "id": document_id,
                "file_sha256": case["sha256"],
                "pages": document_pages,
            }
        )
    draft = {
        "schema_version": 2,
        "status": "review_required",
        "private_corpus": True,
        "redistributable": False,
        "documents": draft_documents,
    }
    packet_key = hashlib.sha256(
        json.dumps(
            draft, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()[:16]
    index = {
        "schema_version": 2,
        "stage": "stage-7.3c-private-block-order-review",
        "private_corpus": True,
        "redistributable": False,
        "must_not_commit": True,
        "contains_private_rendered_pages": True,
        "contains_extracted_text_in_json": False,
        "coordinate_space": "display_space_from_layout_to_display_v1",
        "visual_labels": "blind_neutral_blocks_v2",
        "interactive_file": "review.html",
        "interactive_files": {
            "launcher": "review.html",
            "reviewer_a": "reviewer-a.html",
            "reviewer_b": "reviewer-b.html",
            "adjudication": "adjudicate.html",
        },
        "reviewer_isolation": "separate_locked_html_v2",
        "ui_workflow": "blind_brush_blocks_erase_split_merge_adjudicate_v2",
        "contains_extracted_text_in_html_metadata": False,
        "packet_key": packet_key,
        "contract_sha256": digest_file(args.contract),
        "quality_report_sha256": digest_file(args.quality_report),
        "documents": len(draft_documents),
        "pages": len(index_pages),
        "items": index_pages,
    }
    (args.output_dir / "manifest.draft.json").write_text(
        json.dumps(draft, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (args.output_dir / "packet-index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    write_interactive_review(args.output_dir, interactive_pages, draft, packet_key)
    readme = """# Private Stage 7.3 order review packet

This directory contains rendered private PDF pages and must not be committed.
Open `review.html` for the three-step workflow. Reviewer A and Reviewer B must
use separate packet copies and their locked `reviewer-a.html` / `reviewer-b.html`
workbenches. Each brushes visible nodes into human reading blocks, sweeps page
furniture, and exports a reviewer-only schema-v2 manifest. Only after both
independent reviews finish,
open `adjudicate.html`, import both files, resolve disagreements, and export the
complete manifest.

`overlays/` contains box-only PNGs for coordinate checks; `pages/` contains the
clean renderings. No extracted document text is copied into JSON or HTML metadata.
Browser persistence stores only node IDs, human block roles, and block precedence.

Validate the final adjudicated manifest with:

python -B tools\\stage12_order_gold.py --manifest <completed.json> --validate-only

The draft remains BLOCKED until every visible node belongs to exactly one block,
all required block precedence, reviewer/adjudication labels, and disagreement
reason codes are complete.
"""
    (args.output_dir / "README.md").write_text(readme, encoding="utf-8")
    print(f"private packet: {args.output_dir}")
    print(f"pages: {len(index_pages)}")


def self_test() -> None:
    assert DEFAULT_WHEEL_DIR.name == "stage7c-bbox-python-exact"
    matrices = [
        ({"a": 1, "b": 0, "c": 0, "d": 1, "e": 0, "f": 0}, (1, 2), (1, 2)),
        ({"a": 0, "b": 1, "c": -1, "d": 0, "e": 100, "f": 0}, (10, 20), (80, 10)),
        ({"a": -1, "b": 0, "c": 0, "d": -1, "e": 100, "f": 200}, (10, 20), (90, 180)),
        ({"a": 0, "b": -1, "c": 1, "d": 0, "e": 0, "f": 100}, (10, 20), (20, 90)),
    ]
    for matrix, point, expected in matrices:
        assert affine_point(matrix, *point) == expected
    box = display_bbox([10, 20, 30, 40], matrices[1][0])
    assert box == (60, 10, 80, 30)
    assert clip_bbox_to_bounds((-5, 10, 120, 90), (0, 0, 100, 100)) == (
        0,
        10,
        100,
        90,
    )
    assert clip_bbox_to_bounds((101, 10, 120, 90), (0, 0, 100, 100)) is None
    assert bbox_values({"x0": 1, "y0": 2, "x1": 3, "y1": 4}) == (1, 2, 3, 4)
    template = REVIEW_UI_TEMPLATE.read_text(encoding="utf-8")
    for token in [
        "__PACKET_PAGES__",
        "__PACKET_MANIFEST__",
        "__PACKET_KEY__",
        "__INITIAL_PROFILE__",
        "__LOCKED_PROFILE__",
    ]:
        assert template.count(token) == 1
    for required in [
        "pointerdown",
        "pointermove",
        "pointerup",
        "commitBrush",
        "commitErase",
        "commitSplit",
        "canonicalLabels",
        "nextOrdinalByPage",
        "/^b(\\d+)$/",
        "Blind mode",
        "block_precedence_pairs",
    ]:
        assert required in template
    for forbidden in [
        "feature_codes",
        "artifact_probability",
        "main_flow_probability",
    ]:
        assert forbidden not in template
    rendered = render_review_ui(
        template,
        [],
        {"schema_version": 2, "documents": []},
        "test-packet",
        initial_profile="reviewer-a",
        locked_profile="reviewer-a",
    )
    assert "__PACKET_" not in rendered
    assert 'const packetKey = "test-packet";' in rendered
    assert "\\u003c/script>" in json_script_payload({"value": "</script>"})
    quality = {
        ("a", 1): {"bigram_f1": 0.8, "character_f1": 1.0},
        ("a", 2): {"bigram_f1": 0.9, "character_f1": 1.0},
        ("b", 1): {"bigram_f1": 0.95, "character_f1": 1.0},
    }
    assert seed_pages(quality, ["a", "b"], 1) == {("a", 1), ("b", 1)}
    print("stage12 Stage 7.3C blind brush review packet self-test: ok")


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.corpus_dir is None:
        raise SystemExit("set --corpus-dir or RUST_PDF_STAGE12_CORPUS_DIR")
    if not args.rust_wheel_dir.is_dir():
        raise FileNotFoundError(
            f"exact Rust wheel directory not found: {args.rust_wheel_dir}"
        )
    if not args.quality_report.is_file():
        raise FileNotFoundError(f"quality report not found: {args.quality_report}")
    contract = load_contract(args.contract)
    cases = [
        (case, verify_case(args.corpus_dir, case)) for case in contract["documents"]
    ]
    report = json.loads(args.quality_report.read_text(encoding="utf-8"))
    quality = quality_index(report)
    document_ids = [str(case["id"]) for case, _ in cases]
    seeds = seed_pages(quality, document_ids, max(1, int(args.minimum_pages)))
    rust_pdf = load_rust_pdf(args.rust_wheel_dir)
    retained, feature_best = collect_pages(cases, rust_pdf, quality, seeds)
    selected = seeds | {value[1] for value in feature_best.values()}
    missing = sorted(selected - set(retained))
    if missing:
        raise ValueError(f"selected review pages were not retained: {missing}")
    write_packet(args, contract, cases, selected, retained)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
