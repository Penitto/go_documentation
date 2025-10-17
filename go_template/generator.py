from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .parser import (
    extract_declarations,
    find_module_info,
    filter_internal_imports,
    parse_functions,
    parse_imports,
    strip_comments_preserve_whitespace,
)
from .repository import build_repository_index
from .template_renderer import render_template


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


def generate_documentation(go_file: Path, output_path: Optional[Path] = None) -> Path:
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
        logging.debug(
            "Parsed %d functions directly from source for %s",
            len(funcs),
            resolved_path,
        )
    for func in funcs:
        func.setdefault("receiver", func.get("receiver", ""))
        func.setdefault("full_name", func.get("full_name") or func.get("name", ""))

    internal_imports = sorted(set(internal_imports))
    logging.debug(
        "Internal imports detected: %s",
        ", ".join(internal_imports) if internal_imports else "none",
    )

    content = render_template(resolved_path, types, consts, vars_, funcs, internal_imports)
    if output_path is None:
        output_path = go_file.with_suffix(go_file.suffix + ".md")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    logging.info("Documentation written to %s", output_path)
    return output_path
