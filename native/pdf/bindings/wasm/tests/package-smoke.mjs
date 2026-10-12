import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve, join } from "node:path";
import { pathToFileURL } from "node:url";

const directory = resolve(process.argv[2] ?? "target/priority-artifacts/wasm");
const api = await import(pathToFileURL(join(directory, "pdf_wasm.js")));
await api.default({ module_or_path: readFileSync(join(directory, "pdf_wasm_bg.wasm")) });
assert.equal(api.versionInfo().version, "0.2.0");
assert.equal(api.buildInfo().development_line, "priority-2026-10-12");
assert.equal(api.buildInfo().native_ocr_onnx, false);

const fixture = (name) => readFileSync(resolve("tests/fixtures/priority", name));
const document = new api.PdfDocument(fixture("selected-pages.pdf"), { max_pages: 3 });
assert.deepEqual(document.extractWithOptions({ pages: "1,3" }).pages.map((page) => page.page_index), [0, 2]);
const events = document.extractLayoutEvents({ pages: "1,3" });
document.free();
const values = [];
for (let next = events.next(); !next.done; next = events.next()) values.push(next.value);
assert.deepEqual(values.map((value) => value.event), ["document_start", "page", "page", "document_finalize"]);
assert.equal(values[2].payload.page_number, 3);
events.free();
assert.throws(() => api.extractWithOptions(fixture("filters.pdf"), { limits: { max_file_bytes: 1 } }), (error) => error.get("code") === "limit_exceeded");
assert.equal(api.extractWithOptions(fixture("filters.pdf"), {}).text, "Filtered text");
assert.deepEqual(api.extractWithOptions(fixture("vertical-w2.pdf"), {}).glyphs[0].advance, [0, -12]);

const scan = new api.PdfDocument(fixture("ocr-pages.pdf"), undefined);
const candidates = scan.directOcrPages({ pages: "2" });
assert.equal(candidates.next().value.page_index, 1);
candidates.close();
assert.equal(candidates.next().done, true);
candidates.free();
scan.free();
console.log("Packaged web WASM smoke: PASS");
