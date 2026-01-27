from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Dict, List
import uuid


def _placeholder() -> str:
    return f"<<FILL {str(uuid.uuid4())[:8]}>>"


INDENT = "    "


def _split_top_level_params(param_display: str) -> List[str]:
    parts: List[str] = []
    buf: List[str] = []
    depth_paren = depth_brack = depth_brace = 0
    i = 0
    length = len(param_display)
    while i < length:
        ch = param_display[i]
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
            i += 1
            continue
        buf.append(ch)
        i += 1
    if buf:
        part = "".join(buf).strip()
        if part:
            parts.append(part)
    return parts


def _normalize_param_entries(param_display: str) -> List[str]:
    if not param_display or param_display == "нет":
        return []
    raw_parts = _split_top_level_params(param_display)
    if not raw_parts:
        return []
    entries: List[str] = []
    pending: List[str] = []

    for part in raw_parts:
        token = part.strip()
        if not token:
            continue
        first, rest = _split_first_token(token)
        if not rest:
            if first and _IDENTIFIER_RE.match(first) and token == first:
                pending.append(first)
            else:
                if pending:
                    entries.extend(pending)
                    pending.clear()
                entries.append(token)
            continue
        type_text = rest
        if pending:
            for name in pending:
                entries.append(f"{name} {type_text}")
            pending.clear()
        entries.append(f"{first} {type_text}".strip())

    if pending:
        entries.extend(pending)
    return entries


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _split_first_token(fragment: str) -> tuple[str, str]:
    fragment = fragment.strip()
    if not fragment:
        return "", ""
    parts = fragment.split(None, 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def _slugify_anchor(text: str) -> str:
    text = text.replace("`", "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "func"


def _doc_path_from_label(file_label: str) -> str:
    if file_label.endswith(".go"):
        return file_label[:-3] + ".doc.md"
    if file_label.endswith(".md"):
        return file_label
    return file_label + ".doc.md"


def _relation_link(label: str) -> str:
    match = re.match(r"^(?P<name>.+?)\s*\((?P<file>[^)]+)\)$", label)
    if match:
        name = match.group("name").strip()
        file_label = match.group("file").strip()
        target = f"{_doc_path_from_label(file_label)}#func-{_slugify_anchor(name)}"
        return f"[{label}]({target})"
    if ":" in label:
        left, right = label.split(":", 1)
        left = left.strip()
        right = right.strip()
        if left.endswith(".go") or "/" in left:
            target = f"{_doc_path_from_label(left)}#func-{_slugify_anchor(right or label)}"
            return f"[{label}]({target})"
    target = f"#func-{_slugify_anchor(label)}"
    return f"[{label}]({target})"


def _link_relation_line(line: str) -> str:
    prefix = line[: len(line) - len(line.lstrip())]
    stripped = line.strip()
    if not stripped.startswith("- "):
        return line.rstrip()
    if stripped.endswith(":"):
        return f"{prefix}{stripped}"
    label = stripped[2:].strip()
    if not label:
        return f"{prefix}{stripped}"
    return f"{prefix}- {_relation_link(label)}"


def _append_structure_group(lines: List[str], title: str, items: List[str]) -> None:
    lines.append(f"- {title}:")
    if items:
        for name in items:
            lines.append(f"{INDENT}- `{name}` — {_placeholder()}")
    else:
        lines.append(f"{INDENT}нет")
    lines.append("")


def _append_type_group(
    lines: List[str],
    title: str,
    types: List[str],
    type_details: Dict[str, dict],
) -> None:
    lines.append(f"- {title}:")
    if not types:
        lines.append(f"{INDENT}нет")
        lines.append("")
        return
    for name in types:
        lines.append(f"{INDENT}- `{name}` — {_placeholder()}")
        detail = type_details.get(name, {})
        kind = detail.get("kind")
        if kind == "struct":
            fields = detail.get("fields") or []
            lines.append(f"{INDENT * 2}- Поля:")
            if fields:
                for field in fields:
                    lines.append(f"{INDENT * 3}- `{field}` — {_placeholder()}")
            else:
                lines.append(f"{INDENT * 3}{_placeholder()}")
        elif kind == "interface":
            methods = detail.get("methods") or []
            lines.append(f"{INDENT * 2}- Методы:")
            if methods:
                for method in methods:
                    lines.append(f"{INDENT * 3}- `{method}` — {_placeholder()}")
            else:
                lines.append(f"{INDENT * 3}{_placeholder()}")
        elif kind and detail.get("underlying"):
            lines.append(f"{INDENT * 2}- Базовый тип: `{detail['underlying']}`")
        else:
            lines.append(f"{INDENT * 2}- Внутренняя структура типа:")
            lines.append(f"{INDENT * 3}{_placeholder()}")
    lines.append("")


def render_template_blocks(
    file_path: Path,
    types: List[str],
    type_details: Dict[str, dict],
    consts: List[str],
    vars_: List[str],
    funcs: List[dict],
    internal_imports: List[str],
    file_callers: List[str],
) -> List[List[str]]:
    blocks: List[List[str]] = []
    blocks.append(
        [
            "## Назначение файла",
            f"- Общая роль в проекте: {_placeholder()}",
            "",
        ]
    )

    structure_lines: List[str] = ["## Внутренняя структура"]
    _append_type_group(
        structure_lines,
        "Ключевые типы (структуры, интерфейсы, алиасы) и их задачи",
        types,
        type_details,
    )
    _append_structure_group(
        structure_lines,
        "Глобальные константы и их значение",
        consts,
    )
    _append_structure_group(
        structure_lines,
        "Глобальные переменные и их значение",
        vars_,
    )
    blocks.append(structure_lines)

    funcs_header: List[str] = ["## Функции и методы"]
    if funcs:
        funcs_header.append("")
        blocks.append(funcs_header)
        for func in funcs:
            receiver_display = f"{func['receiver']} " if func.get("receiver") else ""
            params_raw = func.get("params")
            returns_raw = func.get("returns")
            param_display = params_raw if params_raw else "нет"
            return_display = returns_raw if returns_raw else "нет"
            same_rel = func.get("relationship_same_file", "—")
            other_rel = func.get("relationship_other_files", "—")

            block_lines: List[str] = [
                f"### `func {receiver_display}{func.get('full_name', func.get('name', ''))}`",
                "- Назначение:",
                f"{_placeholder()}",
                "",
            ]
            param_entries = _normalize_param_entries(param_display)
            block_lines.append("- Входные данные:")
            if param_entries:
                for entry in param_entries:
                    block_lines.append(f"{INDENT}- `{entry}` — {_placeholder()}")
            else:
                block_lines.append(f"{INDENT}{_placeholder()}")
            block_lines.append("")
            return_entries = _normalize_param_entries(return_display)
            block_lines.append("- Выходные данные:")
            if return_entries:
                for entry in return_entries:
                    block_lines.append(f"{INDENT}- `{entry}` — {_placeholder()}")
            else:
                block_lines.append(f"{INDENT}{_placeholder()}")
            block_lines.append("")
            read_vars = func.get("read_vars") or []
            write_vars = func.get("write_vars") or []
            block_lines.append("- Считываемые переменные:")
            if read_vars:
                for name in read_vars:
                    block_lines.append(f"{INDENT}- `{name}` — {_placeholder()}")
            else:
                block_lines.append(f"{INDENT}{_placeholder()}")
            block_lines.append("- Записываемые переменные:")
            if write_vars:
                for name in write_vars:
                    block_lines.append(f"{INDENT}- `{name}` — {_placeholder()}")
            else:
                block_lines.append(f"{INDENT}{_placeholder()}")
            block_lines.append("")
            block_lines.append("- Внутренняя логика:")
            block_lines.append(f"{_placeholder()}")
            block_lines.append("")
            block_lines.append("- Связь с бизнес-процессом:")
            block_lines.append(f"{_placeholder()}")
            block_lines.append("")
            block_lines.append(
                "- Взаимосвязь с другими функциями файла:"
            )
            if same_rel != "—":
                for sub_line in same_rel.splitlines():
                    linked = _link_relation_line(sub_line)
                    block_lines.append(f"{INDENT}{linked}")
            else:
                block_lines.append(f"{INDENT}нет")
            block_lines.append(
                "- Взаимосвязь с другими функциями из других файлов:"
            )
            if other_rel != "—":
                for sub_line in other_rel.splitlines():
                    linked = _link_relation_line(sub_line)
                    block_lines.append(f"{INDENT}{linked}")
            else:
                block_lines.append(f"{INDENT}нет")
            block_lines.extend(
                [
                    "",
                ]
            )
            blocks.append(block_lines)
    else:
        funcs_header.append("`<Функции не обнаружены>`")
        funcs_header.append("")
        blocks.append(funcs_header)

    return blocks


def render_template(
    file_path: Path,
    types: List[str],
    type_details: Dict[str, dict],
    consts: List[str],
    vars_: List[str],
    funcs: List[dict],
    internal_imports: List[str],
    file_callers: List[str],
) -> str:
    today = datetime.date.today().strftime("%Y-%m-%d")
    try:
        rel_path = file_path.relative_to(Path.cwd()).as_posix()
    except ValueError:
        rel_path = file_path.as_posix()

    blocks = render_template_blocks(
        file_path,
        types,
        type_details,
        consts,
        vars_,
        funcs,
        internal_imports,
        file_callers,
    )
    lines: List[str] = [line for block in blocks for line in block]

    return "\n".join(lines).strip() + "\n"
