from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Optional, Tuple


def strip_comments_preserve_whitespace(source: str) -> str:
    result = list(source)
    i = 0
    length = len(source)
    while i < length:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < length else ""
        if ch == '"':
            i = _skip_string(source, i, '"')
        elif ch == "'":
            i = _skip_string(source, i, "'")
        elif ch == "`":
            i = _skip_raw_string(source, i)
        elif ch == "/" and nxt == "/":
            j = i
            while j < length and source[j] != "\n":
                result[j] = " "
                j += 1
            i = j
        elif ch == "/" and nxt == "*":
            j = i + 2
            result[i] = result[i + 1] = " "
            while j < length - 1:
                if source[j] == "*" and source[j + 1] == "/":
                    result[j] = result[j + 1] = " "
                    j += 2
                    break
                result[j] = " " if source[j] != "\n" else "\n"
                j += 1
            i = j
        else:
            i += 1
    return "".join(result)


def _skip_string(source: str, start: int, quote: str) -> int:
    i = start + 1
    length = len(source)
    while i < length:
        ch = source[i]
        if ch == "\\":
            i += 2
            continue
        if ch == quote:
            return i + 1
        i += 1
    return length


def _skip_raw_string(source: str, start: int) -> int:
    i = start + 1
    length = len(source)
    while i < length:
        if source[i] == "`":
            return i + 1
        i += 1
    return length


def find_module_info(start: Path) -> Tuple[Optional[str], Optional[Path]]:
    for parent in [start, *start.parents]:
        go_mod = parent / "go.mod"
        if go_mod.is_file():
            for line in go_mod.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("module "):
                    return stripped.split(" ", 1)[1].strip(), parent
            return None, parent
    return None, None


def extract_declarations(source: str) -> Tuple[List[str], List[str], List[str]]:
    types: List[str] = []
    consts: List[str] = []
    vars_: List[str] = []

    length = len(source)
    i = 0
    depth = 0
    while i < length:
        ch = source[i]
        if ch == '"':
            i = _skip_string(source, i, '"')
            continue
        if ch == "'":
            i = _skip_string(source, i, "'")
            continue
        if ch == "`":
            i = _skip_raw_string(source, i)
            continue
        if ch == "{":
            depth += 1
            i += 1
            continue
        if ch == "}":
            depth = max(depth - 1, 0)
            i += 1
            continue

        if depth == 0:
            if _token_at(source, i, "type"):
                i = _parse_type_decl(source, i, types)
                continue
            if _token_at(source, i, "const"):
                i = _parse_const_var_decl(source, i, consts, "const")
                continue
            if _token_at(source, i, "var"):
                i = _parse_const_var_decl(source, i, vars_, "var")
                continue

        i += 1

    return types, consts, vars_


def _extract_identifier_list(fragment: str) -> List[str]:
    if not fragment:
        return []
    stop_chars = ("=", ":=", "+=", "-=", "*=", "/=", "%=", "|=", "&=", "^=", "<<=", ">>=", "&^=")
    head = fragment
    for op in stop_chars:
        if op in head:
            head = head.split(op, 1)[0]
            break
    match = re.match(r"^[\w\s,]+", head)
    if match:
        head = match.group(0)
    identifiers = []
    for part in head.split(","):
        name = part.strip()
        if name:
            primary = name.split(None, 1)[0]
            if primary:
                identifiers.append(primary)
    return identifiers


def _parse_type_decl(source: str, start_idx: int, names: List[str]) -> int:
    idx = start_idx + len("type")
    idx = _skip_whitespace(source, idx)
    length = len(source)
    if idx < length and source[idx] == "(":
        block, idx = extract_balanced(source, idx, "(", ")")
        content = block[1:-1]
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            token = stripped.split(None, 1)[0]
            token = token.rstrip("{(")
            if token:
                names.append(token)
        return _skip_statement_terminators(source, idx)
    identifier, idx = _read_identifier(source, idx)
    if identifier:
        names.append(identifier)
    return idx


def _parse_const_var_decl(source: str, start_idx: int, names: List[str], kind: str) -> int:
    idx = start_idx + len(kind)
    idx = _skip_whitespace(source, idx)
    length = len(source)
    if idx < length and source[idx] == "(":
        block, idx = extract_balanced(source, idx, "(", ")")
        content = block[1:-1]
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            for name in _extract_identifier_list(stripped):
                if name:
                    names.append(name)
        return _skip_statement_terminators(source, idx)
    clause, idx = _read_simple_clause(source, idx)
    for name in _extract_identifier_list(clause):
        if name:
            names.append(name)
    return idx


def _read_identifier(source: str, idx: int) -> Tuple[str, int]:
    length = len(source)
    while idx < length and source[idx] in " \t\r\n":
        idx += 1
    start = idx
    while idx < length and _is_identifier_char(source[idx]):
        idx += 1
    return source[start:idx], idx


def _read_simple_clause(source: str, idx: int) -> Tuple[str, int]:
    length = len(source)
    start = idx
    while idx < length:
        ch = source[idx]
        if ch in "\n;":
            break
        if ch == '"':
            idx = _skip_string(source, idx, '"')
            continue
        if ch == "'":
            idx = _skip_string(source, idx, "'")
            continue
        if ch == "`":
            idx = _skip_raw_string(source, idx)
            continue
        idx += 1
    clause = source[start:idx]
    idx = _skip_statement_terminators(source, idx)
    return clause.strip(), idx


def _skip_statement_terminators(source: str, idx: int) -> int:
    length = len(source)
    while idx < length and source[idx] in " \t\r\n;":
        idx += 1
    return idx


def _token_at(source: str, idx: int, token: str) -> bool:
    end = idx + len(token)
    if end > len(source):
        return False
    if source[idx:end] != token:
        return False
    if idx > 0 and _is_identifier_char(source[idx - 1]):
        return False
    if end < len(source) and _is_identifier_char(source[end]):
        return False
    return True


def _is_identifier_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def extract_balanced(source: str, start: int, open_char: str, close_char: str) -> Tuple[str, int]:
    if start >= len(source) or source[start] != open_char:
        raise ValueError(f"expected {open_char} at position {start}")
    depth = 0
    i = start + 1
    while i < len(source):
        ch = source[i]
        if ch in ('"', "'", "`"):
            if ch == "`":
                i = _skip_raw_string(source, i) - 1
            else:
                i = _skip_string(source, i, ch) - 1
        elif ch == open_char:
            depth += 1
        elif ch == close_char:
            if depth == 0:
                return source[start : i + 1], i + 1
            depth -= 1
        i += 1
    raise ValueError(f"unbalanced {open_char}{close_char} starting at {start}")


def parse_functions(source: str, stripped_source: str) -> List[dict]:
    funcs: List[dict] = []
    depth = 0
    i = 0
    length = len(stripped_source)
    while i < length:
        ch = stripped_source[i]
        if ch == "{":
            depth += 1
            i += 1
            continue
        if ch == "}":
            depth = max(depth - 1, 0)
            i += 1
            continue
        if depth == 0 and stripped_source.startswith("func", i):
            before = stripped_source[i - 1] if i > 0 else " "
            after = stripped_source[i + 4] if i + 4 < length else " "
            if before not in "_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" and after in " \t(":
                func_info, next_i = _parse_single_func(source, i)
                funcs.append(func_info)
                i = next_i
                continue
        i += 1
    return funcs


def _parse_single_func(source: str, start_idx: int) -> Tuple[dict, int]:
    idx = start_idx + len("func")
    length = len(source)

    idx = _skip_whitespace(source, idx)
    receiver = ""
    if idx < length and source[idx] == "(":
        receiver_segment, idx = extract_balanced(source, idx, "(", ")")
        receiver = receiver_segment.strip()
        idx = _skip_whitespace(source, idx)

    name_start = idx
    while idx < length and (source[idx].isalnum() or source[idx] == "_"):
        idx += 1
    name = source[name_start:idx]

    idx = _skip_whitespace(source, idx)

    generics = ""
    if idx < length and source[idx] == "[":
        generics_segment, idx = extract_balanced(source, idx, "[", "]")
        generics = generics_segment.strip()
        idx = _skip_whitespace(source, idx)

    if idx >= length or source[idx] != "(":
        raise ValueError(f"malformed function signature near: {source[start_idx:start_idx+60]!r}")

    params_segment, idx = extract_balanced(source, idx, "(", ")")
    params = params_segment.strip()

    idx = _skip_whitespace(source, idx)
    returns = ""
    if idx < length and source[idx] == "(":
        returns_segment, idx = extract_balanced(source, idx, "(", ")")
        returns = returns_segment.strip()
        idx = _skip_whitespace(source, idx)
    else:
        return_buffer = []
        while idx < length:
            ch = source[idx]
            if ch in "{\n":
                break
            if ch == "/" and idx + 1 < length and source[idx + 1] in "/*":
                break
            return_buffer.append(ch)
            idx += 1
        returns = "".join(return_buffer).strip()
        idx = _skip_whitespace(source, idx)

    body_text = ""
    while idx < length and source[idx] != "{":
        idx += 1
    if idx < length and source[idx] == "{":
        body_segment, idx = extract_balanced(source, idx, "{", "}")
        body_text = body_segment

    full_name = name + (generics if generics else "")
    receiver_type = _extract_receiver_type(receiver)
    return (
        {
            "receiver": receiver,
            "receiver_type": receiver_type,
            "name": name,
            "full_name": full_name,
            "params": params[1:-1].strip() if params.startswith("(") else params,
            "returns": returns[1:-1].strip() if returns.startswith("(") and returns.endswith(")") else returns,
            "body": body_text,
        },
        idx,
    )


def _skip_whitespace(source: str, idx: int) -> int:
    length = len(source)
    while idx < length and source[idx] in " \t\r\n":
        idx += 1
    return idx


def _extract_receiver_type(receiver: str) -> Optional[str]:
    if not receiver:
        return None
    content = receiver.strip()
    if not (content.startswith("(") and content.endswith(")")):
        return None
    inner = content[1:-1].strip()
    if not inner:
        return None
    parts = inner.split()
    if len(parts) >= 2:
        return parts[-1]
    return None


def parse_imports(source: str) -> List[dict]:
    imports: List[dict] = []
    lines = source.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("import "):
            rest = line[6:].strip()
            if rest.startswith("("):
                i += 1
                while i < len(lines):
                    inner = lines[i].strip()
                    if inner.startswith(")"):
                        break
                    entry = _parse_import_entry(inner)
                    if entry:
                        imports.append(entry)
                    i += 1
            else:
                entry = _parse_import_entry(rest)
                if entry:
                    imports.append(entry)
        i += 1
    return imports


def _parse_import_entry(token: str) -> Optional[dict]:
    token = token.split("//", 1)[0].strip()
    if not token:
        return None
    token = re.sub(r"/\*.*?\*/", "", token).strip()
    if not token:
        return None
    parts = token.split()
    if len(parts) == 1:
        path = parts[0]
        alias: Optional[str] = None
    else:
        alias = parts[0]
        path = parts[-1]
    path = path.strip('"`')
    return {"alias": alias, "path": path}


def filter_internal_imports(imports: Iterable[dict], module_path: Optional[str]) -> List[str]:
    if not module_path:
        return []
    prefix = module_path + "/"
    internal = []
    for entry in imports:
        path = entry["path"]
        if path == module_path or path.startswith(prefix):
            internal.append(path)
    return internal


def parse_package_name(source: str) -> Optional[str]:
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("package "):
            parts = stripped.split()
            if len(parts) >= 2:
                return parts[1]
    return None
