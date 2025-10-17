from __future__ import annotations

import re
import logging
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set, Tuple

from .parser import (
    parse_functions,
    parse_imports,
    parse_package_name,
    strip_comments_preserve_whitespace,
)

GO_KEYWORDS: Set[str] = {
    "break",
    "case",
    "chan",
    "const",
    "continue",
    "default",
    "defer",
    "else",
    "fallthrough",
    "for",
    "func",
    "go",
    "goto",
    "if",
    "import",
    "interface",
    "map",
    "package",
    "range",
    "return",
    "select",
    "struct",
    "switch",
    "type",
    "var",
}

GO_BUILTINS: Set[str] = {
    "append",
    "cap",
    "close",
    "complex",
    "copy",
    "delete",
    "imag",
    "len",
    "make",
    "new",
    "panic",
    "print",
    "println",
    "real",
    "recover",
}

CALL_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
SELECTOR_CALL_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*\(")


def build_repository_index(module_root: Optional[Path], module_path: Optional[str]) -> Optional[dict]:
    if not module_root or not module_root.exists():
        return None

    functions: List[dict] = []
    functions_by_file: Dict[Path, List[dict]] = defaultdict(list)
    functions_by_dir_name: Dict[Tuple[Path, str], List[dict]] = defaultdict(list)
    functions_by_import_path_name: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
    functions_by_rel_path_name: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
    file_alias_maps: Dict[Path, dict] = {}
    registry: Dict[Tuple[str, str, str], dict] = {}
    rel_paths_present: Set[str] = set()

    for go_path in _iter_go_files(module_root):
        try:
            source = go_path.read_text(encoding="utf-8")
        except OSError:
            continue
        stripped = strip_comments_preserve_whitespace(source)
        package_name = parse_package_name(source) or ""
        imports = parse_imports(source)
        alias_map, internal_alias_map = _build_alias_maps(imports, module_path)
        file_alias_maps[go_path] = {
            "alias_map": alias_map,
            "internal_alias_map": internal_alias_map,
        }
        import_path = _compute_import_path(module_path, module_root, go_path)
        rel_path = _compute_relative_path(module_root, go_path)
        try:
            file_funcs = parse_functions(source, stripped)
        except ValueError as exc:
            logging.warning("Skipping %s during indexing: %s", go_path, exc)
            continue
        for func in file_funcs:
            func["file_path"] = go_path
            func["dir_path"] = go_path.parent
            func["package"] = package_name
            func["import_path"] = import_path
            func["rel_path"] = rel_path
            key = _make_function_key(func)
            func["key"] = key
            functions.append(func)
            functions_by_file[go_path].append(func)
            registry[key] = func
            functions_by_dir_name[(go_path.parent, func["name"])].append(func)
            if not func.get("receiver_type") and import_path:
                functions_by_import_path_name[(import_path, func["name"])].append(func)
            if rel_path is not None:
                functions_by_rel_path_name[(rel_path, func["name"])].append(func)
                rel_paths_present.add(rel_path)

    call_edges = _build_call_graph(
        functions,
        file_alias_maps,
        functions_by_dir_name,
        functions_by_import_path_name,
        functions_by_rel_path_name,
        rel_paths_present,
        module_path,
    )
    reverse_edges = _invert_call_graph(call_edges)
    attach_relationship_summaries(functions, call_edges, reverse_edges, registry, module_root)

    return {
        "functions": functions,
        "functions_by_file": functions_by_file,
        "registry": registry,
        "call_edges": call_edges,
        "reverse_edges": reverse_edges,
    }


def _iter_go_files(root: Path) -> Iterator[Path]:
    for path in root.rglob("*.go"):
        if path.name.endswith("_test.go"):
            continue
        try:
            rel_parts = path.relative_to(root).parts
        except ValueError:
            rel_parts = path.parts
        if any(part in {"vendor", "testdata"} for part in rel_parts):
            continue
        yield path


def _build_alias_maps(imports: List[dict], module_path: Optional[str]) -> Tuple[Dict[str, str], Dict[str, str]]:
    alias_map: Dict[str, str] = {}
    internal_alias_map: Dict[str, str] = {}
    for entry in imports:
        alias = entry["alias"]
        path = entry["path"]
        if alias in (".", "_"):
            continue
        normalized_alias = alias if alias else path.split("/")[-1]
        alias_map[normalized_alias] = path
        if module_path and (path == module_path or path.startswith(module_path + "/")):
            internal_alias_map[normalized_alias] = path
    return alias_map, internal_alias_map


def _compute_import_path(module_path: Optional[str], module_root: Optional[Path], file_path: Path) -> Optional[str]:
    if not module_path or not module_root:
        return None
    try:
        rel_dir = file_path.parent.relative_to(module_root).as_posix()
    except ValueError:
        return None
    rel_dir = rel_dir.strip(".")
    if not rel_dir:
        return module_path
    rel_dir = rel_dir.strip("/")
    if not rel_dir:
        return module_path
    return f"{module_path}/{rel_dir}"


def _build_call_graph(
    functions: List[dict],
    file_alias_maps: Dict[Path, dict],
    functions_by_dir_name: Dict[Tuple[Path, str], List[dict]],
    functions_by_import_path_name: Dict[Tuple[str, str], List[dict]],
    functions_by_rel_path_name: Dict[Tuple[str, str], List[dict]],
    rel_paths_present: Set[str],
    module_path: Optional[str],
) -> Dict[Tuple[str, str, str], Set[Tuple[str, str, str]]]:
    call_edges: Dict[Tuple[str, str, str], Set[Tuple[str, str, str]]] = defaultdict(set)
    for func in functions:
        body = func.get("body") or ""
        if not body:
            continue
        sanitized = strip_comments_preserve_whitespace(body)
        sanitized = _mask_string_literals(sanitized)
        simple_calls = _find_simple_calls(sanitized)
        for name in simple_calls:
            for target in functions_by_dir_name.get((func["dir_path"], name), []):
                call_edges[func["key"]].add(target["key"])
        file_context = file_alias_maps.get(func["file_path"], {})
        alias_map = file_context.get("alias_map", {})
        selector_calls = _find_selector_calls(sanitized, alias_map)
        for import_path, called_name in selector_calls:
            for target in functions_by_import_path_name.get((import_path, called_name), []):
                call_edges[func["key"]].add(target["key"])
            for rel_path in _match_import_to_rel_paths(
                import_path,
                rel_paths_present,
                module_path,
            ):
                for target in functions_by_rel_path_name.get((rel_path, called_name), []):
                    call_edges[func["key"]].add(target["key"])
    return call_edges


def _invert_call_graph(
    call_edges: Dict[Tuple[str, str, str], Set[Tuple[str, str, str]]]
) -> Dict[Tuple[str, str, str], Set[Tuple[str, str, str]]]:
    reverse_edges: Dict[Tuple[str, str, str], Set[Tuple[str, str, str]]] = defaultdict(set)
    for caller, callees in call_edges.items():
        for callee in callees:
            reverse_edges[callee].add(caller)
    return reverse_edges


def _find_simple_calls(body: str) -> Set[str]:
    names: Set[str] = set()
    for match in CALL_PATTERN.finditer(body):
        name = match.group(1)
        start = match.start(1)
        if start > 0 and body[start - 1] == ".":
            continue
        if name in GO_KEYWORDS or name in GO_BUILTINS:
            continue
        names.add(name)
    return names


def _find_selector_calls(body: str, alias_map: Dict[str, str]) -> Set[Tuple[str, str]]:
    if not alias_map:
        return set()
    calls: Set[Tuple[str, str]] = set()
    for match in SELECTOR_CALL_PATTERN.finditer(body):
        alias = match.group(1)
        name = match.group(2)
        if alias in alias_map:
            calls.add((alias_map[alias], name))
    return calls


def _mask_string_literals(source: str) -> str:
    chars = list(source)
    i = 0
    length = len(source)
    while i < length:
        ch = source[i]
        if ch == '"':
            i = _mask_quoted_string(chars, source, i, '"')
        elif ch == "'":
            i = _mask_quoted_string(chars, source, i, "'")
        elif ch == "`":
            i = _mask_raw_string_literal(chars, source, i)
        else:
            i += 1
    return "".join(chars)


def _mask_quoted_string(chars: List[str], source: str, start: int, quote: str) -> int:
    i = start + 1
    length = len(source)
    while i < length:
        ch = source[i]
        chars[i] = " "
        if ch == "\\":
            if i + 1 < length:
                chars[i + 1] = " "
            i += 2
            continue
        if ch == quote:
            return i + 1
        i += 1
    return length


def _mask_raw_string_literal(chars: List[str], source: str, start: int) -> int:
    i = start + 1
    length = len(source)
    while i < length:
        ch = source[i]
        if ch == "`":
            return i + 1
        chars[i] = " " if ch != "\n" else "\n"
        i += 1
    return length


def attach_relationship_summaries(
    functions: List[dict],
    call_edges: Dict[Tuple[str, str, str], Set[Tuple[str, str, str]]],
    reverse_edges: Dict[Tuple[str, str, str], Set[Tuple[str, str, str]]],
    registry: Dict[Tuple[str, str, str], dict],
    module_root: Optional[Path],
) -> None:
    for func in functions:
        same_file_calls: List[str] = []
        other_calls: List[str] = []
        for callee_key in call_edges.get(func["key"], set()):
            target = registry.get(callee_key)
            if not target:
                continue
            label = _format_function_label(target, func, module_root)
            if target["file_path"] == func["file_path"]:
                same_file_calls.append(label)
            else:
                other_calls.append(label)

        same_file_callers: List[str] = []
        other_callers: List[str] = []
        for caller_key in reverse_edges.get(func["key"], set()):
            caller = registry.get(caller_key)
            if not caller:
                continue
            label = _format_function_label(caller, func, module_root)
            if caller["file_path"] == func["file_path"]:
                same_file_callers.append(label)
            else:
                other_callers.append(label)

        func["relationship_same_file"] = _summarize_relations(same_file_calls, same_file_callers)
        func["relationship_other_files"] = _summarize_relations(other_calls, other_callers)


def _summarize_relations(calls: List[str], callers: List[str]) -> str:
    parts: List[str] = []
    unique_calls = _sorted_unique(calls)
    if unique_calls:
        parts.append("вызывает: " + ", ".join(unique_calls))
    unique_callers = _sorted_unique(callers)
    if unique_callers:
        parts.append("вызывается: " + ", ".join(unique_callers))
    return "; ".join(parts) if parts else "—"


def _sorted_unique(items: List[str]) -> List[str]:
    return sorted(set(items), key=str.lower)


def _format_function_label(target: dict, current_func: dict, module_root: Optional[Path]) -> str:
    if target.get("receiver_type"):
        receiver_display = _format_receiver_display(target["receiver_type"])
        base = f"{receiver_display}.{target['name']}"
    else:
        base = target["name"]

    if target["file_path"] == current_func["file_path"]:
        return base
    if target["file_path"].parent == current_func["file_path"].parent:
        return f"{base} ({target['file_path'].name})"

    import_path = target.get("import_path")
    current_import_path = current_func.get("import_path")
    if import_path and import_path != current_import_path:
        suffix = import_path.split("/")[-1]
        if suffix:
            return f"{suffix}.{base}"
        return f"{import_path}.{base}"

    if module_root:
        rel = _relative_path(target["file_path"], module_root)
        return f"{rel}:{base}"
    return f"{target['file_path'].as_posix()}:{base}"


def _format_receiver_display(receiver_type: Optional[str]) -> str:
    if not receiver_type:
        return ""
    display = receiver_type.strip()
    while display.startswith("*"):
        display = display[1:]
    if "[" in display:
        display = display.split("[", 1)[0]
    if "." in display:
        display = display.split(".")[-1]
    return display or receiver_type


def _relative_path(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def _make_function_key(func: dict) -> Tuple[str, str, str]:
    receiver = func.get("receiver_type") or ""
    return (str(func.get("file_path")), func["name"], receiver)


def _compute_relative_path(module_root: Path, file_path: Path) -> Optional[str]:
    try:
        rel = file_path.parent.relative_to(module_root).as_posix()
        return rel or ""
    except ValueError:
        return None


def _match_import_to_rel_paths(
    import_path: str,
    rel_paths_present: Set[str],
    module_path: Optional[str],
) -> Set[str]:
    matches: Set[str] = set()
    if not import_path:
        return matches
    candidates: List[str] = []
    segments = import_path.split("/")
    for i in range(len(segments)):
        suffix = "/".join(segments[i:])
        candidates.append(suffix)
    if module_path:
        prefix = module_path + "/"
        if import_path.startswith(prefix):
            rel_candidate = import_path[len(prefix) :]
            candidates.append(rel_candidate)
    for candidate in candidates:
        if candidate in rel_paths_present:
            matches.add(candidate)
    return matches
