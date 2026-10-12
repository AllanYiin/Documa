"""Seed only the new bounded fuzz targets with original public fixtures."""

from pathlib import Path

root = Path(__file__).resolve().parents[1]
for target in ("layout_document", "images_ocr"):
    output = root / "fuzz/corpus" / target
    output.mkdir(parents=True, exist_ok=True)
    for source in (root / "tests/fixtures/priority").glob("*.pdf"):
        destination = output / source.name
        if not destination.exists():
            destination.write_bytes(source.read_bytes())

output = root / "fuzz/corpus/decode_filters"
output.mkdir(parents=True, exist_ok=True)
for name, data in {
    "hex": b"\x00414243>",
    "ascii85": b"\x01z~>",
    "runlength": bytes([2, 129, 65, 128]),
    "lzw": bytes.fromhex("03 80 0b 60 50 22 0c 0c 85 01"),
}.items():
    destination = output / name
    if not destination.exists():
        destination.write_bytes(data)
