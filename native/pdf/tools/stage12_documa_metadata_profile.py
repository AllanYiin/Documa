#!/usr/bin/env python3
"""Privacy-safe Stage 6D profile of mapped Documa metadata and RSS phases."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterable

import psutil


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOCUMA_ROOT = ROOT.parent / "Documa"
DEFAULT_WHEEL_DIR = ROOT / "target" / "stage6c2e-final-python-exact"
DEFAULT_OUTPUT = ROOT / "target" / "stage12-stage6d-metadata-profile" / "report.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=os.getenv("RUST_PDF_REAL_AI_INDEX"),
    )
    parser.add_argument("--documa-root", type=Path, default=DEFAULT_DOCUMA_ROOT)
    parser.add_argument("--rust-wheel-dir", type=Path, default=DEFAULT_WHEEL_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verbose-metadata", action="store_true")
    return parser.parse_args()


class RssSampler:
    def __init__(self) -> None:
        self.process = psutil.Process()
        self.phase = "startup"
        self.peaks: dict[str, int] = collections.defaultdict(int)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def __enter__(self) -> "RssSampler":
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join()
        self.record()

    def set_phase(self, phase: str) -> None:
        self.record()
        self.phase = phase
        self.record()

    def record(self) -> None:
        rss = self.process.memory_info().rss
        self.peaks[self.phase] = max(self.peaks[self.phase], rss)

    def _sample(self) -> None:
        while not self._stop.wait(0.005):
            self.record()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8", errors="replace")


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def metadata_owners(document: Any) -> Iterable[tuple[str, dict[str, Any]]]:
    yield "document", document.metadata
    for page in document.pages:
        yield "page", page.metadata
        for block in page.blocks:
            yield "block", block.metadata
            for span in block.spans:
                yield "span", span.metadata
        for image in page.images:
            yield "image", image.metadata


def metadata_profile(document: Any, to_plain_data: Any) -> dict[str, Any]:
    totals: collections.Counter[str] = collections.Counter()
    counts: collections.Counter[str] = collections.Counter()
    owner_counts: collections.Counter[str] = collections.Counter()
    empty_counts: collections.Counter[str] = collections.Counter()
    for owner, metadata in metadata_owners(document):
        owner_counts[owner] += 1
        for key, value in metadata.items():
            field = f"{owner}.{key}"
            encoded = canonical_bytes({key: to_plain_data(value)})
            totals[field] += max(0, len(encoded) - 2)
            counts[field] += 1
            if value is None or value is False or value == [] or value == {}:
                empty_counts[field] += 1
    fields = [
        {
            "field": field,
            "occurrences": counts[field],
            "encoded_bytes": byte_count,
            "empty_occurrences": empty_counts[field],
        }
        for field, byte_count in totals.most_common()
    ]
    return {
        "owner_counts": dict(sorted(owner_counts.items())),
        "metadata_field_count": sum(counts.values()),
        "metadata_encoded_bytes_sum": sum(totals.values()),
        "fields": fields,
    }


def main() -> int:
    args = parse_args()
    if args.source is None or not args.source.is_file():
        raise FileNotFoundError("A readable --source or RUST_PDF_REAL_AI_INDEX is required.")
    if not (args.documa_root / "src").is_dir():
        raise FileNotFoundError(f"Documa source not found: {args.documa_root}")
    sys.path.insert(0, str(args.rust_wheel_dir))
    sys.path.insert(0, str(args.documa_root / "src"))

    from documa.adapters.base import ParseOptions
    from documa.adapters.rust_pdf_adapter import RustPdfAdapter
    from documa.core.ir import to_plain_data

    options = ParseOptions(
        normalize_unicode=True,
        extract_images=True,
        resolve_relations=True,
        asset_dir=None,
        metadata={
            "rust_pdf_include_verbose_metadata": args.verbose_metadata,
        },
    )
    started = time.perf_counter()
    with RssSampler() as sampler:
        sampler.set_phase("parse")
        document = RustPdfAdapter().parse(args.source, options)
        parse_seconds = time.perf_counter() - started

        sampler.set_phase("metadata_scan")
        profile = metadata_profile(document, to_plain_data)

        sampler.set_phase("canonical_serialization")
        value = to_plain_data(document)
        value["id"] = "<document>"
        value["source_name"] = "<private-pdf>"
        serialized = canonical_bytes(value)

        sampler.set_phase("result")
        report = {
            "schema_version": 1,
            "privacy": {
                "contains_extracted_content": False,
                "contains_source_path": False,
            },
            "source": {
                "sha256": digest_file(args.source),
                "bytes": args.source.stat().st_size,
                "pages": len(document.pages),
            },
            "mode": (
                "verbose"
                if args.verbose_metadata
                else "compact"
            ),
            "timing": {
                "parse_seconds": parse_seconds,
                "total_seconds": time.perf_counter() - started,
            },
            "rss_peak_bytes_by_phase": dict(sorted(sampler.peaks.items())),
            "canonical": {
                "serialized_bytes": len(serialized),
                "sha256": hashlib.sha256(serialized).hexdigest(),
            },
            "metadata": profile,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(canonical_bytes(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
