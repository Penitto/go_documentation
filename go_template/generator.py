from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple
import re

from .parser import (
    extract_declarations,
    extract_type_details,
    find_module_info,
    filter_internal_imports,
    parse_functions,
    parse_imports,
    strip_comments_preserve_whitespace,
)
from .repository import GO_BUILTINS, GO_KEYWORDS, build_repository_index
from .template_renderer import render_template, render_template_blocks


FUNC_HEADER_PATTERN = re.compile(r"^### `func\s+(.+?)`$")
IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
ASSIGN_OP_PATTERN = re.compile(r":=|<<=|>>=|&\^=|\+=|-=|\*=|/=|%=|&=|\|=|\^=|=")
INC_DEC_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*(\+\+|--)")
PREDECLARED_TYPES = {
    "any",
    "bool",
    "byte",
    "complex64",
    "complex128",
    "error",
    "float32",
    "float64",
    "int",
    "int8",
    "int16",
    "int32",
    "int64",
    "rune",
    "string",
    "uint",
    "uint8",
    "uint16",
    "uint32",
    "uint64",
    "uintptr",
}
PREDECLARED_IDENTIFIERS = {
    "false",
    "iota",
    "nil",
    "true",
}


@dataclass
class TemplateBlockMeta:
    name: str
    kind: str
    start_line: int
    end_line: int
    length: int
    lines: List[str]
    path: str


def _classify_block(block: List[str]) -> Tuple[str, str]:
    if not block:
        return "unknown", ""
    first = block[0].strip()
    if first.startswith("### `func"):
        match = FUNC_HEADER_PATTERN.match(first)
        return "func", match.group(1).strip() if match else first
    if first.startswith("## "):
        return "section", first[3:].strip()
    return "section", first


def _split_top_level_params(signature: str) -> List[str]:
    parts: List[str] = []
    buf: List[str] = []
    depth_paren = depth_brack = depth_brace = 0
    for ch in signature:
        if ch == "(":
            depth_paren += 1
        elif ch == ")":
            depth_paren = max(depth_paren - 1, 0)
        elif ch == "[":
            depth_brack += 1
        elif ch == "]":
            depth_brack = max(depth_brack - 1, 0)
        elif ch == "{":
            depth_brace += 1
        elif ch == "}":
            depth_brace = max(depth_brace - 1, 0)
        if ch == "," and depth_paren == depth_brack == depth_brace == 0:
            part = "".join(buf).strip()
            if part:
                parts.append(part)
            buf = []
            continue
        buf.append(ch)
    if buf:
        part = "".join(buf).strip()
        if part:
            parts.append(part)
    return parts


def _is_identifier(token: str) -> bool:
    return bool(IDENTIFIER_PATTERN.fullmatch(token)) and token not in GO_KEYWORDS


def _split_first_token(fragment: str) -> Tuple[str, str]:
    fragment = fragment.strip()
    if not fragment:
        return "", ""
    parts = fragment.split(None, 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def _extract_param_names(signature: str) -> List[str]:
    if not signature:
        return []
    pending: List[str] = []
    names: List[str] = []
    for part in _split_top_level_params(signature):
        first, rest = _split_first_token(part)
        if not first:
            continue
        if rest:
            if pending:
                names.extend([name for name in pending if _is_identifier(name)])
                pending.clear()
            if _is_identifier(first):
                names.append(first)
        else:
            if _is_identifier(first):
                pending.append(first)
    return names


def _extract_import_aliases(imports: List[dict]) -> List[str]:
    aliases: List[str] = []
    for entry in imports:
        alias = entry.get("alias")
        path = entry.get("path", "")
        if alias in (".", "_"):
            continue
        if not alias:
            alias = path.split("/")[-1]
        if alias:
            aliases.append(alias)
    return aliases


def _extract_receiver_name(receiver: str) -> Optional[str]:
    if not receiver:
        return None
    receiver = receiver.strip()
    if receiver.startswith("(") and receiver.endswith(")"):
        receiver = receiver[1:-1].strip()
    if not receiver:
        return None
    parts = receiver.split()
    if len(parts) >= 2 and _is_identifier(parts[0]):
        return parts[0]
    return None


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


def _extract_identifiers(expr: str) -> Iterable[str]:
    for match in IDENTIFIER_PATTERN.finditer(expr):
        name = match.group()
        if name in GO_KEYWORDS or name in GO_BUILTINS:
            continue
        if match.start() > 0 and expr[match.start() - 1] == ".":
            continue
        yield name


def _infer_read_write_vars(
    func: dict,
    global_vars: List[str],
    global_consts: List[str],
    type_names: List[str],
    import_aliases: List[str],
    function_names: List[str],
) -> Tuple[List[str], List[str]]:
    body = func.get("body") or ""
    if not body:
        return [], []
    sanitized = strip_comments_preserve_whitespace(body)
    sanitized = _mask_string_literals(sanitized)

    exclude_names = (
        set(type_names)
        | set(import_aliases)
        | set(function_names)
        | set(PREDECLARED_TYPES)
        | set(PREDECLARED_IDENTIFIERS)
    )

    reads: set[str] = set()
    writes: set[str] = set()
    lhs_spans: List[Tuple[int, int]] = []

    for match in ASSIGN_OP_PATTERN.finditer(sanitized):
        op = match.group()
        if op == "=":
            prev_char = sanitized[match.start() - 1] if match.start() > 0 else ""
            next_char = sanitized[match.end()] if match.end() < len(sanitized) else ""
            if prev_char in ("=", "!", ">", "<") or next_char == "=":
                continue
        lhs_start = max(sanitized.rfind("\n", 0, match.start()), sanitized.rfind(";", 0, match.start()))
        lhs_start = lhs_start + 1 if lhs_start != -1 else 0
        lhs = sanitized[lhs_start:match.start()]
        lhs_spans.append((lhs_start, match.start()))
        rhs_end = sanitized.find("\n", match.end())
        semi_end = sanitized.find(";", match.end())
        if rhs_end == -1 or (semi_end != -1 and semi_end < rhs_end):
            rhs_end = semi_end
        if rhs_end == -1:
            rhs_end = len(sanitized)
        rhs = sanitized[match.end():rhs_end]

        lhs_names = [
            name
            for name in _extract_identifiers(lhs)
            if name != "_" and name not in exclude_names
        ]
        for name in lhs_names:
            writes.add(name)
            if op not in ("=", ":="):
                reads.add(name)

    for match in INC_DEC_PATTERN.finditer(sanitized):
        name = match.group(1)
        if name in exclude_names:
            continue
        reads.add(name)
        writes.add(name)
        lhs_spans.append((match.start(1), match.end(1)))

    # Capture reads from the rest of the body (conditions, returns, calls, etc.),
    # ignoring identifiers that appear only on assignment LHS.
    for match in IDENTIFIER_PATTERN.finditer(sanitized):
        name = match.group()
        if name in GO_KEYWORDS or name in GO_BUILTINS:
            continue
        if name in exclude_names:
            continue
        if match.start() > 0 and sanitized[match.start() - 1] == ".":
            continue
        if any(start <= match.start() < end for start, end in lhs_spans):
            continue
        if _is_field_key(sanitized, match.end()):
            continue
        if _is_call_expression(sanitized, match.end()):
            continue
        reads.add(name)

    return sorted(reads), sorted(writes)


def _is_field_key(source: str, end_idx: int) -> bool:
    i = end_idx
    length = len(source)
    while i < length and source[i].isspace():
        i += 1
    if i < length and source[i] == ":":
        if i + 1 < length and source[i + 1] == "=":
            return False
        return True
    return False


def _is_call_expression(source: str, end_idx: int) -> bool:
    i = end_idx
    length = len(source)
    while i < length and source[i].isspace():
        i += 1
    return i < length and source[i] == "("


def _find_repository_root(start: Path) -> Path:
    start = start.resolve()
    for parent in [start, *start.parents]:
        if (parent / ".git").is_dir():
            logging.debug("Detected repository root via .git at %s", parent)
            return parent
    cwd = Path.cwd().resolve()
    try:
        start.relative_to(cwd)
        logging.debug("Using current working directory as repository root: %s", cwd)
        return cwd
    except ValueError:
        logging.debug("Falling back to file directory as repository root: %s", start)
        return start


def _prepare_render_inputs(
    go_file: Path,
) -> Tuple[Path, List[str], Dict[str, dict], List[str], List[str], List[dict], List[str], List[str]]:
    if not go_file.is_file():
        raise FileNotFoundError(f"{go_file} is not a file")

    logging.info("Generating documentation for %s", go_file)
    source = go_file.read_text(encoding="utf-8")
    stripped = strip_comments_preserve_whitespace(source)
    types, consts, vars_ = extract_declarations(stripped)
    type_details = extract_type_details(stripped)
    logging.debug(
        "Extracted declarations: %d types, %d consts, %d vars",
        len(types),
        len(consts),
        len(vars_),
    )
    module_path, module_root = find_module_info(go_file.parent.resolve())
    if module_root is None:
        module_root = _find_repository_root(go_file.parent)
        logging.info("Module path not found; using repository root %s", module_root)
    else:
        logging.info("Detected module %s at %s", module_path, module_root)
    imports = parse_imports(source)
    internal_imports = filter_internal_imports(imports, module_path)

    repo_index = build_repository_index(module_root, module_path)
    if repo_index is None:
        logging.warning(
            "Repository index unavailable for %s; falling back to local analysis",
            go_file,
        )
    else:
        logging.info(
            "Repository index built: %d functions",
            len(repo_index.get("functions", [])),
        )
    resolved_path = go_file.resolve()
    if repo_index:
        funcs = list(repo_index["functions_by_file"].get(resolved_path, []))
        logging.debug(
            "Fetched %d functions from index for %s",
            len(funcs),
            resolved_path,
        )
    else:
        funcs = []
    if not funcs:
        try:
            funcs = parse_functions(source, stripped)
        except ValueError as exc:
            logging.error("Failed to parse %s: %s", resolved_path, exc)
            raise
        for func in funcs:
            func.setdefault("relationship_same_file", "—")
            func.setdefault("relationship_other_files", "—")
            func.setdefault("other_file_calls_list", [])
            func.setdefault("other_file_callers_list", [])
        logging.debug(
            "Parsed %d functions directly from source for %s",
            len(funcs),
            resolved_path,
        )
    import_aliases = _extract_import_aliases(imports)
    func_names = [func.get("name", "") for func in funcs if func.get("name")]

    for func in funcs:
        func.setdefault("receiver", func.get("receiver", ""))
        func.setdefault("full_name", func.get("full_name") or func.get("name", ""))
        func.setdefault("other_file_calls_list", [])
        func.setdefault("other_file_callers_list", [])
        read_vars, write_vars = _infer_read_write_vars(
            func,
            vars_,
            consts,
            types,
            import_aliases,
            func_names,
        )
        func["read_vars"] = read_vars
        func["write_vars"] = write_vars

    other_callers: List[str] = sorted(
        {label for func in funcs for label in func.get("other_file_callers_list", [])},
        key=str.lower,
    )

    internal_imports = sorted(set(internal_imports))
    logging.debug(
        "Internal imports detected: %s",
        ", ".join(internal_imports) if internal_imports else "none",
    )
    return (
        resolved_path,
        types,
        type_details,
        consts,
        vars_,
        funcs,
        internal_imports,
        other_callers,
    )


def generate_documentation(go_file: Path, output_path: Optional[Path] = None) -> Path:
    (
        resolved_path,
        types,
        type_details,
        consts,
        vars_,
        funcs,
        internal_imports,
        other_callers,
    ) = _prepare_render_inputs(go_file)

    content = render_template(
        resolved_path,
        types,
        type_details,
        consts,
        vars_,
        funcs,
        internal_imports,
        other_callers,
    )
    if output_path is None:
        output_path = go_file.with_name(f"{go_file.stem}.doc.md")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    logging.info("Documentation written to %s", output_path)
    return output_path


def generate_documentation_iter(
    go_file: Path,
    output_path: Optional[Path] = None,
    block_path_resolver: Optional[Callable[[str, str, int], Path]] = None,
):
    """Yield template blocks as they are written to disk.

    Итератор возвращает метаданные по блокам (включая разделы "Назначение файла",
    "Внутренняя структура" и каждую функцию) с номерами строк в итоговом файле.
    Можно задать block_path_resolver(name, kind, index) → Path, чтобы выводить
    разные блоки в разные файлы (нумерация строк будет в пределах целевого файла).
    """
    (
        resolved_path,
        types,
        type_details,
        consts,
        vars_,
        funcs,
        internal_imports,
        other_callers,
    ) = _prepare_render_inputs(go_file)

    blocks = render_template_blocks(
        resolved_path,
        types,
        type_details,
        consts,
        vars_,
        funcs,
        internal_imports,
        other_callers,
    )
    if output_path is None:
        output_path = go_file.with_name(f"{go_file.stem}.doc.md")

    # Если блоки идут в разные файлы, считаем номера строк отдельно для каждого пути.
    line_counters: Dict[Path, int] = {}
    initialized_paths: set[Path] = set()

    for idx, block in enumerate(blocks):
        kind, name = _classify_block(block)
        target_path = (
            block_path_resolver(name, kind, idx)
            if block_path_resolver is not None
            else output_path
        )
        if target_path is None:
            target_path = output_path
        target_path = Path(target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        start_line = line_counters.get(target_path, 1)
        length = len(block)
        end_line = start_line + length - 1 if length else start_line - 1

        mode = "a" if target_path in initialized_paths else "w"
        with target_path.open(mode, encoding="utf-8") as fh:
            for line in block:
                fh.write(line + "\n")

        meta = TemplateBlockMeta(
            name=name,
            kind=kind,
            start_line=start_line,
            end_line=end_line,
            length=length,
            lines=list(block),
            path=str(target_path),
        )
        yield meta

        line_counters[target_path] = end_line + 1
        initialized_paths.add(target_path)
    logging.info("Documentation written to %s", output_path)
