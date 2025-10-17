from __future__ import annotations

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
            return parent
    cwd = Path.cwd().resolve()
    try:
        start.relative_to(cwd)
        return cwd
    except ValueError:
        return start


def generate_documentation(go_file: Path, output_path: Optional[Path] = None) -> Path:
    if not go_file.is_file():
        raise FileNotFoundError(f"{go_file} is not a file")

    source = go_file.read_text(encoding="utf-8")
    stripped = strip_comments_preserve_whitespace(source)
    types, consts, vars_ = extract_declarations(stripped)
    module_path, module_root = find_module_info(go_file.parent.resolve())
    if module_root is None:
        module_root = _find_repository_root(go_file.parent)
    imports = parse_imports(source)
    internal_imports = filter_internal_imports(imports, module_path)

    repo_index = build_repository_index(module_root, module_path)
    resolved_path = go_file.resolve()
    if repo_index:
        funcs = list(repo_index["functions_by_file"].get(resolved_path, []))
    else:
        funcs = []
    if not funcs:
        funcs = parse_functions(source, stripped)
        for func in funcs:
            func.setdefault("relationship_same_file", "—")
            func.setdefault("relationship_other_files", "—")
    for func in funcs:
        func.setdefault("receiver", func.get("receiver", ""))
        func.setdefault("full_name", func.get("full_name") or func.get("name", ""))

    internal_imports = sorted(set(internal_imports))

    content = render_template(resolved_path, types, consts, vars_, funcs, internal_imports)
    if output_path is None:
        output_path = go_file.with_suffix(go_file.suffix + ".md")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    return output_path
