"""Authoritative Python AST adapter for the repository code graph."""

from __future__ import annotations

import ast
import json
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from documa.codegraph.models import (
    CODE_GRAPH_ANALYZER_VERSION,
    CodeEdge,
    CodeEdgeType,
    CodeNode,
    CodeNodeKind,
    CodeOccurrence,
    CodeSpan,
    EdgeResolution,
    ParsedCodeFile,
    ParseStatus,
    edge_id,
    sha256_bytes,
    sha256_text,
    stable_id,
)


def _posix_relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _module_name(path: Path, source_root: Path) -> str:
    relative = path.relative_to(source_root)
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or source_root.name


def _span(node: ast.AST) -> CodeSpan:
    return CodeSpan(
        start_line=int(getattr(node, "lineno", 1)),
        end_line=int(getattr(node, "end_lineno", getattr(node, "lineno", 1))),
        start_column=int(getattr(node, "col_offset", 0)),
        end_column=int(getattr(node, "end_col_offset", 0)),
    )


def _span_text(lines: list[str], span: CodeSpan) -> str:
    return "\n".join(lines[span.start_line - 1 : span.end_line])


def _expr_path(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        left = _expr_path(node.value)
        return f"{left}.{node.attr}" if left else node.attr
    if isinstance(node, ast.Call):
        target = _expr_path(node.func)
        return f"{target}()" if target else None
    if isinstance(node, ast.Subscript):
        return _expr_path(node.value)
    try:
        return ast.unparse(node)
    except (ValueError, TypeError):
        return None


def _signature(node: ast.AST) -> str:
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(filter(None, (_expr_path(base) for base in node.bases)))
        return f"class {node.name}({bases})" if bases else f"class {node.name}"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        args = ast.unparse(node.args)
        returns = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
        return f"{prefix} {node.name}({args}){returns}"
    return ""


def _summary(kind: CodeNodeKind, signature: str, docstring: str | None, decorators: list[str]) -> str:
    first = ""
    if docstring:
        first = docstring.strip().split("\n\n", 1)[0].replace("\n", " ").strip()
    pieces = [signature]
    if first:
        pieces.append(first)
    if decorators:
        pieces.append("Decorators: " + ", ".join(decorators))
    if len(pieces) == 1:
        pieces.append(f"Python {kind.value}.")
    return " — ".join(pieces)


def _role_for_path(relative_path: str, display_name: str) -> str | None:
    parts = PurePosixPath(relative_path).parts
    if any(part in {"test", "tests"} for part in parts) or display_name.startswith("test_"):
        return "test"
    return None


def _is_type_checking(test: ast.AST) -> bool:
    value = _expr_path(test) or ""
    return value in {"TYPE_CHECKING", "typing.TYPE_CHECKING"}


def _import_classification(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.If):
            return "type_checking" if _is_type_checking(current.test) else "conditional"
        if isinstance(current, ast.Try):
            if any(
                handler.type is not None and (_expr_path(handler.type) or "").endswith("ImportError")
                for handler in current.handlers
            ):
                return "optional"
            return "conditional"
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return "local"
        current = parents.get(current)
    return "runtime"


class _ScopedCallVisitor(ast.NodeVisitor):
    """Visit one executable scope without attributing nested definitions."""

    def __init__(self) -> None:
        self.calls: list[ast.Call] = []
        self.assignments: dict[str, str] = {}
        self.monkey_patches: list[ast.AST] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return None

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return None

    def visit_Call(self, node: ast.Call) -> None:
        self.calls.append(node)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        inferred = None
        if isinstance(node.value, ast.Call):
            inferred = _expr_path(node.value.func)
        if inferred:
            for target in node.targets:
                name = _expr_path(target)
                if name:
                    self.assignments[name] = inferred
        for target in node.targets:
            if isinstance(target, ast.Attribute) and not (_expr_path(target) or "").startswith(("self.", "cls.")):
                self.monkey_patches.append(target)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        name = _expr_path(node.target)
        annotation = _expr_path(node.annotation)
        if name and annotation:
            self.assignments[name] = annotation
        self.generic_visit(node)


class PythonCodeAdapter:
    name = "python-ast"
    version = CODE_GRAPH_ANALYZER_VERSION

    def supports(self, path: str) -> bool:
        return Path(path).suffix.casefold() == ".py"

    def parse(self, root: str, source_root: str, path: str, workspace_id: str) -> ParsedCodeFile:
        root_path = Path(root).resolve()
        source_root_path = Path(source_root).resolve()
        source_path = Path(path).resolve()
        if root_path not in source_path.parents:
            raise ValueError(f"Code source escapes workspace root: {source_path}")
        relative_path = _posix_relative(source_path, root_path)
        file_id = stable_id("cf", workspace_id, relative_path)
        raw = source_path.read_bytes()
        digest = sha256_bytes(raw)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            return self._unavailable(file_id, relative_path, digest, "CODE_ENCODING_INVALID", str(exc))
        try:
            tree = ast.parse(text, filename=relative_path, type_comments=True)
        except SyntaxError as exc:
            return self._unavailable(file_id, relative_path, digest, "CODE_SYNTAX_ERROR", str(exc))

        module = _module_name(source_path, source_root_path)
        lines = text.splitlines()
        whole_span = CodeSpan(1, max(1, len(lines)), 0, 0)
        module_id = stable_id("cm", workspace_id, module)
        role = _role_for_path(relative_path, source_path.name)
        file_node = CodeNode(
            node_id=file_id,
            kind=CodeNodeKind.FILE,
            qualified_name=relative_path,
            display_name=source_path.name,
            source_locator=relative_path,
            content_hash=digest,
            span=whole_span,
            file_id=file_id,
            role=role,
            summary=f"Python source file for module {module}.",
            metadata={"language": "python", "module": module},
        )
        module_node = CodeNode(
            node_id=module_id,
            kind=CodeNodeKind.MODULE,
            qualified_name=module,
            display_name=module.rsplit(".", 1)[-1],
            source_locator=relative_path,
            content_hash=digest,
            span=whole_span,
            file_id=file_id,
            parent_id=file_id,
            role=role,
            summary=f"Python module {module}.",
            metadata={"language": "python", "module": module},
        )
        result = ParsedCodeFile(
            file_id=file_id,
            relative_path=relative_path,
            language="python",
            digest=digest,
            parse_status=ParseStatus.OK,
            nodes=[file_node, module_node],
        )
        result.structural_edges.append(
            self._edge(file_id, module_id, CodeEdgeType.DEFINES, file_id, whole_span, digest)
        )

        ast_to_node: dict[ast.AST, CodeNode] = {}
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent

        explicit_exports = self._explicit_exports(tree)

        def collect(statements: Iterable[ast.stmt], parent_id: str, qprefix: str, in_class: bool = False) -> None:
            for statement in statements:
                if not isinstance(statement, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if isinstance(statement, ast.ClassDef):
                    kind = CodeNodeKind.CLASS
                elif in_class:
                    kind = CodeNodeKind.METHOD
                else:
                    kind = CodeNodeKind.FUNCTION
                qualified = f"{qprefix}.{statement.name}"
                node_span = _span(statement)
                body = _span_text(lines, node_span)
                decorators = [value for item in statement.decorator_list if (value := _expr_path(item))]
                signature = _signature(statement)
                docstring = ast.get_docstring(statement, clean=False)
                node = CodeNode(
                    node_id=stable_id("cs", workspace_id, kind.value, qualified),
                    kind=kind,
                    qualified_name=qualified,
                    display_name=statement.name,
                    source_locator=relative_path,
                    content_hash=sha256_text(body),
                    span=node_span,
                    file_id=file_id,
                    parent_id=parent_id,
                    signature=signature,
                    docstring=docstring,
                    summary=_summary(kind, signature, docstring, decorators),
                    role=_role_for_path(relative_path, statement.name),
                    metadata={"language": "python", "module": module, "decorators": decorators},
                )
                result.nodes.append(node)
                ast_to_node[statement] = node
                result.structural_edges.append(
                    self._edge(parent_id, node.node_id, CodeEdgeType.CONTAINS, file_id, node_span, digest)
                )
                result.structural_edges.append(
                    self._edge(parent_id, node.node_id, CodeEdgeType.DEFINES, file_id, node_span, digest)
                )
                if parent_id == module_id and (
                    statement.name in explicit_exports if explicit_exports is not None else not statement.name.startswith("_")
                ):
                    result.structural_edges.append(
                        self._edge(module_id, node.node_id, CodeEdgeType.EXPORTS, file_id, node_span, digest)
                    )
                collect(statement.body, node.node_id, qualified, isinstance(statement, ast.ClassDef))

        collect(tree.body, module_id, module)
        self._collect_imports(tree, parents, module_id, file_id, relative_path, digest, result)
        self._collect_bases_and_decorators(ast_to_node, file_id, relative_path, digest, result)
        self._collect_calls(tree, ast_to_node, module_id, file_id, relative_path, digest, result)
        return result

    @staticmethod
    def _explicit_exports(tree: ast.Module) -> set[str] | None:
        for statement in tree.body:
            if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
                continue
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
                continue
            value = statement.value
            if isinstance(value, (ast.List, ast.Tuple, ast.Set)) and all(
                isinstance(item, ast.Constant) and isinstance(item.value, str) for item in value.elts
            ):
                return {str(item.value) for item in value.elts}
        return None

    def _collect_imports(
        self,
        tree: ast.Module,
        parents: dict[ast.AST, ast.AST],
        module_id: str,
        file_id: str,
        relative_path: str,
        digest: str,
        result: ParsedCodeFile,
    ) -> None:
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            classification = _import_classification(node, parents)
            if isinstance(node, ast.Import):
                imports = [(alias.name, None, alias.asname or alias.name.split(".", 1)[0], 0) for alias in node.names]
            else:
                imports = [
                    (node.module or "", alias.name, alias.asname or alias.name, int(node.level or 0))
                    for alias in node.names
                ]
            for imported_module, imported_name, alias, level in imports:
                occurrence_span = _span(node)
                text = imported_module + (f":{imported_name}" if imported_name else "")
                occurrence = CodeOccurrence(
                    occurrence_id=stable_id(
                        "co", file_id, "import", str(occurrence_span.start_line), text, alias
                    ),
                    file_id=file_id,
                    source_node_id=module_id,
                    role="import",
                    span=occurrence_span,
                    text=text,
                    metadata={
                        "module": imported_module,
                        "name": imported_name,
                        "alias": alias,
                        "level": level,
                        "classification": classification,
                        "sourceLocator": relative_path,
                        "sourceDigest": digest,
                    },
                )
                result.occurrences.append(occurrence)
                if imported_name == "*":
                    result.blindspots.append(
                        self._blindspot("STAR_IMPORT", file_id, relative_path, occurrence_span, text)
                    )

    def _collect_bases_and_decorators(
        self,
        ast_to_node: dict[ast.AST, CodeNode],
        file_id: str,
        relative_path: str,
        digest: str,
        result: ParsedCodeFile,
    ) -> None:
        for syntax_node, code_node in ast_to_node.items():
            if isinstance(syntax_node, ast.ClassDef):
                for base in syntax_node.bases:
                    self._reference_occurrence(
                        result, file_id, code_node.node_id, "base", base, relative_path, digest
                    )
            if isinstance(syntax_node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in syntax_node.decorator_list:
                    self._reference_occurrence(
                        result, file_id, code_node.node_id, "decorator", decorator, relative_path, digest
                    )

    def _collect_calls(
        self,
        tree: ast.Module,
        ast_to_node: dict[ast.AST, CodeNode],
        module_id: str,
        file_id: str,
        relative_path: str,
        digest: str,
        result: ParsedCodeFile,
    ) -> None:
        scopes: list[tuple[ast.AST, str, str | None]] = [(tree, module_id, None)]
        class_types: dict[str, dict[str, str]] = {}
        for syntax_node, code_node in ast_to_node.items():
            if isinstance(syntax_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                class_qname = None
                if code_node.kind == CodeNodeKind.METHOD:
                    class_qname = code_node.qualified_name.rsplit(".", 1)[0]
                scopes.append((syntax_node, code_node.node_id, class_qname))
                if class_qname and syntax_node.name == "__init__":
                    initializer = _ScopedCallVisitor()
                    for statement in syntax_node.body:
                        initializer.visit(statement)
                    class_types[class_qname] = {
                        name: value
                        for name, value in initializer.assignments.items()
                        if name.startswith(("self.", "cls."))
                    }
        for syntax_node, source_node_id, class_qname in scopes:
            visitor = _ScopedCallVisitor()
            statements = syntax_node.body if isinstance(syntax_node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef)) else []
            for statement in statements:
                visitor.visit(statement)
            if class_qname:
                visitor.assignments.update(class_types.get(class_qname, {}))
            if isinstance(syntax_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for argument in [*syntax_node.args.posonlyargs, *syntax_node.args.args, *syntax_node.args.kwonlyargs]:
                    annotation = _expr_path(argument.annotation)
                    if annotation:
                        visitor.assignments[argument.arg] = annotation
            for call in visitor.calls:
                call_span = _span(call)
                target_path = _expr_path(call.func)
                metadata: dict[str, Any] = {
                    "target": target_path,
                    "class": class_qname,
                    "localTypes": visitor.assignments,
                    "sourceLocator": relative_path,
                    "sourceDigest": digest,
                }
                if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Call):
                    metadata["receiverType"] = _expr_path(call.func.value.func)
                    metadata["method"] = call.func.attr
                occurrence = CodeOccurrence(
                    occurrence_id=stable_id(
                        "co", file_id, "call", source_node_id, str(call_span.start_line), target_path or "dynamic"
                    ),
                    file_id=file_id,
                    source_node_id=source_node_id,
                    role="call",
                    span=call_span,
                    text=target_path or "<dynamic-call>",
                    metadata=metadata,
                )
                result.occurrences.append(occurrence)
                dynamic_name = (target_path or "").split(".")[-1].removesuffix("()")
                if target_path is None or dynamic_name in {"eval", "exec", "getattr", "setattr", "__import__"}:
                    result.blindspots.append(
                        self._blindspot("DYNAMIC_CALL", file_id, relative_path, call_span, target_path or "<dynamic>")
                    )
                callback_target = (target_path or "").split(".")[-1].removesuffix("()")
                if callback_target in {"register", "add_handler", "connect", "subscribe", "on"} and call.args:
                    callback = _expr_path(call.args[0])
                    if callback:
                        result.occurrences.append(
                            CodeOccurrence(
                                occurrence_id=stable_id(
                                    "co", file_id, "callback", source_node_id, str(call_span.start_line), callback
                                ),
                                file_id=file_id,
                                source_node_id=source_node_id,
                                role="callback",
                                span=call_span,
                                text=callback,
                                metadata={
                                    "target": callback,
                                    "registration": target_path,
                                    "sourceLocator": relative_path,
                                    "sourceDigest": digest,
                                },
                            )
                        )
            for target in visitor.monkey_patches:
                result.blindspots.append(
                    self._blindspot("MONKEY_PATCH", file_id, relative_path, _span(target), _expr_path(target) or "")
                )

    @staticmethod
    def _reference_occurrence(
        result: ParsedCodeFile,
        file_id: str,
        source_node_id: str,
        role: str,
        syntax_node: ast.AST,
        relative_path: str,
        digest: str,
    ) -> None:
        value = _expr_path(syntax_node) or "<dynamic>"
        occurrence_span = _span(syntax_node)
        result.occurrences.append(
            CodeOccurrence(
                occurrence_id=stable_id(
                    "co", file_id, role, source_node_id, str(occurrence_span.start_line), value
                ),
                file_id=file_id,
                source_node_id=source_node_id,
                role=role,
                span=occurrence_span,
                text=value,
                metadata={
                    "target": value,
                    "sourceLocator": relative_path,
                    "sourceDigest": digest,
                },
            )
        )

    @staticmethod
    def _edge(
        source: str,
        target: str,
        edge_type: CodeEdgeType,
        file_id: str,
        span: CodeSpan,
        evidence_hash: str,
    ) -> CodeEdge:
        return CodeEdge(
            edge_id=edge_id(source, target, edge_type),
            source_node_id=source,
            target_node_id=target,
            type=edge_type,
            resolution=EdgeResolution.EXACT,
            resolver=CODE_GRAPH_ANALYZER_VERSION,
            evidence_file_id=file_id,
            evidence_span=span,
            evidence_hash=evidence_hash,
        )

    @staticmethod
    def _blindspot(code: str, file_id: str, locator: str, span: CodeSpan, expression: str) -> dict[str, Any]:
        return {
            "blindspot_id": stable_id("cb", file_id, code, str(span.start_line), expression),
            "file_id": file_id,
            "code": code,
            "source_locator": locator,
            "span": {
                "startLine": span.start_line,
                "endLine": span.end_line,
                "startColumn": span.start_column,
                "endColumn": span.end_column,
            },
            "expression": expression,
        }

    @staticmethod
    def _unavailable(
        file_id: str,
        relative_path: str,
        digest: str,
        code: str,
        message: str,
    ) -> ParsedCodeFile:
        return ParsedCodeFile(
            file_id=file_id,
            relative_path=relative_path,
            language="python",
            digest=digest,
            parse_status=ParseStatus.UNAVAILABLE,
            error_code=code,
            error_message=message,
            blindspots=[
                {
                    "blindspot_id": stable_id("cb", file_id, code),
                    "file_id": file_id,
                    "code": code,
                    "source_locator": relative_path,
                    "span": {"startLine": 1, "endLine": 1},
                    "expression": "",
                }
            ],
        )


def occurrence_metadata_json(occurrence: CodeOccurrence) -> str:
    return json.dumps(occurrence.metadata, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
