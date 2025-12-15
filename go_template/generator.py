from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
import re

from .parser import (
    extract_declarations,
    find_module_info,
    filter_internal_imports,
    parse_functions,
    parse_imports,
    strip_comments_preserve_whitespace,
)
from .repository import build_repository_index
from .template_renderer import render_template, render_template_blocks


FUNC_HEADER_PATTERN = re.compile(r"^### `func\s+(.+?)`$")


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


def _prepare_render_inputs(go_file: Path) -> Tuple[Path, List[str], List[str], List[str], List[dict], List[str], List[str]]:
    if not go_file.is_file():
        raise FileNotFoundError(f"{go_file} is not a file")

    logging.info("Generating documentation for %s", go_file)
    source = go_file.read_text(encoding="utf-8")
    stripped = strip_comments_preserve_whitespace(source)
    types, consts, vars_ = extract_declarations(stripped)
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
    for func in funcs:
        func.setdefault("receiver", func.get("receiver", ""))
        func.setdefault("full_name", func.get("full_name") or func.get("name", ""))
        func.setdefault("other_file_calls_list", [])
        func.setdefault("other_file_callers_list", [])

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
        consts,
        vars_,
        funcs,
        internal_imports,
        other_callers,
    ) = _prepare_render_inputs(go_file)

    content = render_template(
        resolved_path,
        types,
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
        consts,
        vars_,
        funcs,
        internal_imports,
        other_callers,
    ) = _prepare_render_inputs(go_file)

    blocks = render_template_blocks(
        resolved_path,
        types,
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
