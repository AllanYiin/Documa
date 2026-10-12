"""Measure sparse-grid parsing in fresh processes; writes auditable local/CI evidence."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import zipfile


def fixture(path, rows):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<Types/>')
        z.writestr("xl/workbook.xml", '<workbook xmlns:r="urn:r"><sheets><sheet name="Data" sheetId="1" r:id="sheet"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships><Relationship Id="sheet" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet.xml"/></Relationships>')
        cells = ''.join(f'<row r="{i + 1}"><c r="A{i + 1}" t="inlineStr"><is><t>Row {i:08x}</t></is></c><c r="XFD{i + 1}"><v>{i}</v></c></row>' for i in range(rows))
        z.writestr("xl/worksheets/sheet.xml", '<worksheet><sheetData>' + cells + '</sheetData></worksheet>')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', type=Path, default=Path('reports/scaling.json'))
    p.add_argument('--rows', type=int, default=10000)
    p.add_argument('--worker', type=Path)
    args = p.parse_args()
    if args.worker:
        import psutil
        import rust_office
        start = time.perf_counter()
        count = 0
        for event in rust_office.open(args.worker, sparse=True, batch_rows=500, extract_images=False):
            if event['event'] == 'unit':
                count += sum(len(b['metadata'].get('cell_records', [])) for b in event['unit']['blocks'])
        elapsed = time.perf_counter() - start
        memory = psutil.Process().memory_info()
        if os.name == 'nt':
            peak = memory.peak_wset
        else:
            import resource
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if sys.platform != 'darwin':
                peak *= 1024
        print(json.dumps({'seconds': elapsed, 'peak_rss_bytes': peak, 'cell_count': count, 'version': rust_office.version_info()}))
        return 0
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / 'sparse.xlsx'
        fixture(path, args.rows)
        samples = [json.loads(subprocess.check_output([sys.executable, __file__, '--worker', str(path)], text=True)) for _ in range(3)]
    correct = all(s['cell_count'] == 2 * args.rows for s in samples)
    report = {'platform': platform.platform(), 'python': platform.python_version(), 'rows': args.rows, 'columns_in_extent': 16384, 'populated_cells': 2 * args.rows, 'status': 'PASS' if correct else 'FAIL', 'median_seconds': statistics.median(s['seconds'] for s in samples), 'peak_rss_bytes': max(s['peak_rss_bytes'] for s in samples), 'samples': samples, 'limitations': ['Atomic parse retains the completed IR; this is not constant-memory streaming.', 'Timings and RSS are observations, not cross-platform performance guarantees.']}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    return int(not correct)


if __name__ == '__main__':
    raise SystemExit(main())
