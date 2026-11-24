"""Parse Cargo manifests and Rust source without importing or running the repository."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from documa.codegraph.models import (
    CodeEdge, CodeEdgeType, CodeNode, CodeNodeKind, CodeOccurrence, CodeSpan, EdgeResolution,
    ParseStatus, ParsedCodeFile, edge_id, sha256_bytes, sha256_text, stable_id,
)


_RUST_PATH = re.compile(r"(?:r#)?\w+(?:::(?:r#)?\w+)*", re.UNICODE)
_EXPORT_NAME = re.compile(r'\bname\s*=\s*"([^"\\]+)"')
_PY_ATTRIBUTE = re.compile(r"\b(pyfunction|pyclass|pymodule)\b")
_REGISTER_FUNCTION = re.compile(r"\.add_function\s*\(\s*wrap_pyfunction!\s*\(\s*([\w:]+)\s*,")
_REGISTER_CLASS = re.compile(r"\.add_class\s*::\s*<\s*([\w:]+)\s*>")


def read_cargo(path: Path) -> dict[str, Any]:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    with path.open("rb") as source:
        return tomllib.load(source)


def _source_span(node: Any, start: Any = None) -> CodeSpan:
    start = start or node.start_point
    end = node.end_point
    return CodeSpan(start.row + 1, end.row + (1 if end.column else 0), start.column, end.column)


def _text(node: Any) -> str:
    return node.text.decode("utf-8") if node else ""


def _descendants(node: Any, kind: str, *, stop_at_function: bool) -> Iterator[Any]:
    stack = [node]
    while stack:
        child = stack.pop()
        if child.type == kind:
            yield child
        if not stop_at_function or child.type != "function_item":
            stack.extend(reversed(child.named_children))


class RustCodeAdapter:
    name = "tree-sitter-rust"
    version = "tree-sitter-rust-v1"

    @classmethod
    def capability_version(cls) -> str:
        try:
            return f"{cls.version}:{version('tree-sitter')}:{version('tree-sitter-rust')}"
        except PackageNotFoundError:
            return f"{cls.version}:grammar-unavailable"

    def __init__(self) -> None:
        self._parser = None
        self._manifests: dict[Path, dict[str, Any]] = {}
        try:
            import tree_sitter_rust
            from tree_sitter import Language, Parser

            self._parser = Parser(Language(tree_sitter_rust.language()))
        except (ImportError, ValueError):
            pass

    def supports(self, path: str) -> bool:
        source = Path(path)
        return source.suffix == ".rs" or source.name == "Cargo.toml"

    def _cargo(self, path: Path) -> dict[str, Any]:
        if path not in self._manifests:
            self._manifests[path] = read_cargo(path)
        return self._manifests[path]

    def parse(self, root: str, source_root: str, path: str, workspace_id: str) -> ParsedCodeFile:
        repository = Path(root).resolve()
        source = Path(path).resolve()
        relative = source.relative_to(repository).as_posix()
        raw = source.read_bytes()
        file_id = stable_id("cf", workspace_id, relative)
        language = "cargo" if source.name == "Cargo.toml" else "rust"
        result = ParsedCodeFile(file_id, relative, language, sha256_bytes(raw), ParseStatus.OK)
        try:
            text = raw.decode("utf-8")
            result.nodes.append(CodeNode(
                file_id, CodeNodeKind.FILE, relative, source.name, relative, result.digest,
                span=CodeSpan(1, max(1, len(text.splitlines()))), file_id=file_id,
                metadata={"language": language},
            ))
            if language == "cargo":
                self._parse_manifest(source, workspace_id, text, result)
            else:
                self._parse_rust(repository, source, workspace_id, raw, text, result)
        except (ImportError, ValueError) as exc:
            result.parse_status = ParseStatus.UNAVAILABLE
            result.error_code = "CODE_RUST_PARSE_UNAVAILABLE"
            result.error_message = str(exc)
        return result

    def _parse_manifest(self, source: Path, workspace_id: str, text: str, result: ParsedCodeFile) -> None:
        manifest = self._cargo(source)
        name = manifest.get("package", {}).get("name")
        workspace = manifest.get("workspace", {})
        if not name and not workspace:
            return
        locator = result.relative_path
        crate_name = str(name or source.parent.name)
        span = CodeSpan(1, max(1, len(text.splitlines())))
        crate_id = stable_id("crc", workspace_id, locator)
        result.nodes.append(CodeNode(
            crate_id, CodeNodeKind.CRATE, crate_name.replace("-", "_"), crate_name, locator,
            sha256_text("\n".join(text.splitlines())),
            span=span, file_id=result.file_id, parent_id=result.file_id,
            summary=f"Cargo {'package' if name else 'workspace'} {crate_name}.",
            metadata={"language": "cargo", "manifest": locator, "package": name,
                      "workspaceMembers": workspace.get("members", []), "library": manifest.get("lib", {}).get("name")},
        ))
        self._structural_link(result, result.file_id, crate_id, span)

        groups = [(None, manifest), ("workspace", workspace)]
        groups.extend((condition, values) for condition, values in manifest.get("target", {}).items())
        source_lines = text.splitlines()
        for condition, values in groups:
            for group in ("dependencies", "dev-dependencies", "build-dependencies"):
                for alias, entry in values.get(group, {}).items():
                    dependency = entry if isinstance(entry, dict) else {"version": entry}
                    pattern = re.compile(rf"^\s*['\"]?{re.escape(alias)}['\"]?\s*=")
                    line = next((index for index, value in enumerate(source_lines, 1) if pattern.match(value)), 1)
                    result.occurrences.append(CodeOccurrence(
                        stable_id("co", result.file_id, "cargo_dependency", str(condition), group, alias),
                        result.file_id, crate_id, "cargo_dependency", CodeSpan(line, line), alias,
                        {"alias": alias, "package": dependency.get("package", alias), "path": dependency.get("path"),
                         "inherits": dependency.get("workspace", False), "condition": condition, "group": group,
                         "sourceLocator": locator, "sourceDigest": result.digest},
                    ))

    @staticmethod
    def _structural_link(result: ParsedCodeFile, parent: str, target: str, span: CodeSpan) -> None:
        result.structural_edges.append(CodeEdge(
            edge_id(parent, target, CodeEdgeType.CONTAINS), parent, target, CodeEdgeType.CONTAINS,
            EdgeResolution.EXACT, RustCodeAdapter.version, evidence_file_id=result.file_id,
            evidence_span=span, evidence_hash=result.digest,
        ))

    def _parse_rust(
        self, repository: Path, source: Path, workspace_id: str, raw: bytes, text: str, result: ParsedCodeFile,
    ) -> None:
        if self._parser is None:
            result.parse_status = ParseStatus.UNAVAILABLE
            result.error_code = "CODE_RUST_GRAMMAR_UNAVAILABLE"
            result.error_message = "Install the code extra to parse Rust source with Tree-sitter."
            return
        manifest = next((path / "Cargo.toml" for path in source.parents
                         if (path == repository or repository in path.parents) and (path / "Cargo.toml").is_file()), None)
        cargo = self._cargo(manifest) if manifest else {}
        package = str(cargo.get("package", {}).get("name") or (manifest.parent.name if manifest else repository.name))
        crate = package.replace("-", "_")
        source_base = manifest.parent if manifest else repository
        if (source_base / "src") in source.parents:
            source_base = source_base / "src"
        modules = list(source.relative_to(source_base).with_suffix("").parts)
        if modules[-1] in {"lib", "mod"}:
            modules.pop()
        prefix = "::".join([crate, *modules])
        manifest_locator = manifest.relative_to(repository).as_posix() if manifest else None
        lines = text.splitlines()
        identities: Counter[tuple[str, str]] = Counter()

        def build(kind: CodeNodeKind, name: str, qualified: str, parent: str, span: CodeSpan, extra: dict) -> CodeNode:
            key = (kind.value, qualified)
            identities[key] += 1
            body = "\n".join(lines[span.start_line - 1:span.end_line])
            node = CodeNode(
                stable_id("crs", workspace_id, result.relative_path, *key, str(identities[key])),
                kind, qualified, name, result.relative_path, sha256_text(body), span=span,
                file_id=result.file_id, parent_id=parent, summary=f"Rust {kind.value} {qualified}.",
                metadata={"language": "rust", "crate": crate, "manifest": manifest_locator, **extra},
            )
            result.nodes.append(node)
            self._structural_link(result, parent, node.node_id, span)
            return node

        module = build(CodeNodeKind.RUST_MODULE, prefix.split("::")[-1], prefix, result.file_id,
                       CodeSpan(1, max(1, len(lines))), {"module": prefix})

        def record(source_node: CodeNode, role: str, target: str, item: Any, **metadata: Any) -> None:
            result.occurrences.append(CodeOccurrence(
                stable_id("co", source_node.node_id, role, str(item.start_byte), target),
                result.file_id, source_node.node_id, role, _source_span(item), target,
                {"sourceLocator": result.relative_path, "sourceDigest": result.digest, **metadata},
            ))

        def visit(container: Any, qualified: str, module_name: str, parent: str, in_impl: bool = False) -> None:
            attributes = []
            for item in container.named_children:
                if item.type == "attribute_item":
                    attributes.append(item)
                    continue
                if item.type in {"line_comment", "block_comment"}:
                    continue
                attrs, attributes = attributes, []
                name = _text(item.child_by_field_name("name"))
                body = item.child_by_field_name("body")
                if item.type == "impl_item" and body:
                    receiver = _text(item.child_by_field_name("type")).split("<", 1)[0].split("::")[-1]
                    visit(body, f"{qualified}::{receiver}", module_name, parent, in_impl=True)
                elif item.type == "mod_item" and name and body:
                    child_name = f"{qualified}::{name}"
                    child = build(CodeNodeKind.RUST_MODULE, name, child_name, parent, _source_span(item), {"module": child_name})
                    visit(body, child_name, child_name, child.node_id)
                elif item.type == "use_declaration":
                    argument = _text(item.child_by_field_name("argument"))
                    if argument:
                        record(module, "rust_use", argument, item, module=module_name)
                elif item.type in {"function_item", "struct_item", "enum_item", "trait_item"} and name:
                    kind = CodeNodeKind.CLASS
                    if item.type == "function_item":
                        kind = CodeNodeKind.METHOD if in_impl else CodeNodeKind.FUNCTION
                    attrs_text = " ".join(_text(attr) for attr in attrs)
                    py_attribute = _PY_ATTRIBUTE.search(attrs_text)
                    export = _EXPORT_NAME.search(attrs_text)
                    extra = {"module": module_name, "public": any(child.type == "visibility_modifier" for child in item.named_children)}
                    if py_attribute:
                        extra.update(pyRole=py_attribute[1], pyName=export[1] if export else name)
                    definition = build(kind, name, f"{qualified}::{name}", parent,
                                       _source_span(item, attrs[0].start_point if attrs else None), extra)
                    if kind != CodeNodeKind.CLASS and body:
                        for call in _descendants(body, "call_expression", stop_at_function=True):
                            callee = _text(call.child_by_field_name("function"))
                            if _RUST_PATH.fullmatch(callee):
                                record(definition, "rust_call", callee, call, module=module_name)
                            if extra.get("pyRole") == "pymodule":
                                function = _REGISTER_FUNCTION.search(_text(call)) if callee.endswith(".add_function") else None
                                cls = _REGISTER_CLASS.fullmatch(callee.removeprefix(callee.split(".", 1)[0]))
                                target = function or cls
                                if target:
                                    record(definition, "rust_export", target[1], call, pyModule=extra["pyName"],
                                           pyRole="pyfunction" if function else "pyclass", module=module_name)

        syntax = self._parser.parse(raw).root_node
        visit(syntax, prefix, prefix, module.node_id)
        if syntax.has_error:
            result.blindspots.append({
                "blindspot_id": stable_id("cb", result.file_id, "rust-syntax"), "file_id": result.file_id,
                "code": "RUST_SYNTAX_INCOMPLETE", "source_locator": result.relative_path,
                "span": {"startLine": 1, "endLine": len(lines)}, "expression": "Tree-sitter error nodes",
            })
