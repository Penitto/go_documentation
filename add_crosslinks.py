#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


FUNC_NAME_RE = re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_][A-Za-z0-9_]*)")
LINK_RE = re.compile(r"\[[^\]]+\]\([^)]+\)")
REF_RE = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:\s*\((?P<file>[^)]+)\))?$")

RELATION_SAME = "same"
RELATION_OTHER = "other"
RELATION_HEADERS = {
    "взаимосвязь с другими функциями файла": RELATION_SAME,
    "взаимосвязь с другими функциями из других файлов": RELATION_OTHER,
}
SKIP_ITEMS = {"нет", "<нет>"}


@dataclass
class DocIndex:
    by_doc: Dict[Path, Dict[str, str]]
    by_go_file: Dict[str, List[Path]]
    by_go_variant: Dict[Tuple[str, str], Path]
    by_func: Dict[str, List[Path]]
    variant_by_doc: Dict[Path, str]

    def find_doc_for_go_file(self, go_file: str, preferred_variant: Optional[str]) -> Optional[Path]:
        go_key = go_file.lower()
        if preferred_variant is not None:
            hit = self.by_go_variant.get((go_key, preferred_variant))
            if hit:
                return hit
        hit = self.by_go_variant.get((go_key, ""))
        if hit:
            return hit
        hits = self.by_go_file.get(go_key, [])
        return hits[0] if len(hits) == 1 else None

    def find_unique_doc_for_func(
        self,
        func_name: str,
        exclude: Optional[Path],
        preferred_variant: Optional[str],
    ) -> Optional[Path]:
        hits = [path for path in self.by_func.get(func_name, []) if path != exclude]
        if preferred_variant is not None:
            variant_hits = [path for path in hits if self.variant_by_doc.get(path) == preferred_variant]
            if len(variant_hits) == 1:
                return variant_hits[0]
            if len(variant_hits) > 1:
                return None
        return hits[0] if len(hits) == 1 else None


def _extract_header_text(line: str) -> Optional[str]:
    stripped = line.strip()
    if not stripped.startswith("### "):
        return None
    text = stripped[4:].strip()
    if text.startswith("`") and text.endswith("`") and len(text) > 1:
        text = text[1:-1]
    return text.strip() if text else None


def _extract_func_name(header_text: str) -> Optional[str]:
    match = FUNC_NAME_RE.match(header_text)
    return match.group(1) if match else None


def _slugify_heading(text: str) -> str:
    value = text.replace("`", "").lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def _collect_doc_files(root: Path) -> List[Path]:
    return sorted(path.resolve() for path in root.rglob("*.doc.md"))


def _doc_variant(doc_path: Path) -> Tuple[Optional[str], str]:
    name = doc_path.name
    if not name.endswith(".doc.md"):
        return None, ""
    stem = name[:-len(".doc.md")]
    parts = stem.split(".")
    if not parts:
        return None, ""
    go_file = f"{parts[0]}.go".lower()
    variant = ".".join(parts[1:]) if len(parts) > 1 else ""
    return go_file, variant


def _build_index(doc_files: Iterable[Path]) -> DocIndex:
    by_doc: Dict[Path, Dict[str, str]] = {}
    by_go_file: Dict[str, List[Path]] = {}
    by_go_variant: Dict[Tuple[str, str], Path] = {}
    by_func: Dict[str, List[Path]] = {}
    variant_by_doc: Dict[Path, str] = {}

    for doc_path in doc_files:
        lines = doc_path.read_text(encoding="utf-8").splitlines()
        func_map: Dict[str, str] = {}
        for line in lines:
            header_text = _extract_header_text(line)
            if not header_text:
                continue
            func_name = _extract_func_name(header_text)
            if not func_name:
                continue
            anchor = _slugify_heading(header_text)
            func_map[func_name] = anchor
            by_func.setdefault(func_name, []).append(doc_path)
        by_doc[doc_path] = func_map
        go_file, variant = _doc_variant(doc_path)
        if go_file:
            by_go_file.setdefault(go_file, []).append(doc_path)
            by_go_variant.setdefault((go_file, variant), doc_path)
            variant_by_doc[doc_path] = variant

    return DocIndex(
        by_doc=by_doc,
        by_go_file=by_go_file,
        by_go_variant=by_go_variant,
        by_func=by_func,
        variant_by_doc=variant_by_doc,
    )


def _parse_reference(text: str) -> Tuple[Optional[str], Optional[str]]:
    cleaned = text.replace("`", "").strip()
    if cleaned.endswith("()"):
        cleaned = cleaned[:-2].rstrip()
    match = REF_RE.match(cleaned)
    if not match:
        return None, None
    name = match.group("name")
    file_hint = match.group("file")
    if file_hint:
        file_hint = Path(file_hint.strip()).name
    return name, file_hint


def _detect_relation_scope(line: str) -> Optional[str]:
    stripped = line.strip()
    if not stripped.startswith("-"):
        return None
    text = stripped[1:].strip().rstrip(":").strip()
    scope = RELATION_HEADERS.get(text.lower())
    return scope


def _format_link(
    indent: str,
    display: str,
    anchor: str,
    target_doc: Optional[Path],
    current_doc: Path,
    line_ending: str,
) -> str:
    if target_doc is None or target_doc == current_doc:
        return f"{indent}- [{display}](#{anchor}){line_ending}"
    rel_path = os.path.relpath(target_doc, start=current_doc.parent)
    rel_path = rel_path.replace(os.sep, "/")
    return f"{indent}- [{display}]({rel_path}#{anchor}){line_ending}"


def _maybe_link_item(
    line: str,
    scope: str,
    index: DocIndex,
    current_doc: Path,
) -> str:
    stripped = line.lstrip()
    indent = line[: len(line) - len(stripped)]
    if not stripped.startswith("-"):
        return line
    content = stripped[1:].strip()
    if not content or content.endswith(":"):
        return line
    if LINK_RE.search(content):
        return line
    if content.lower() in SKIP_ITEMS:
        return line

    display = content.replace("`", "").strip()
    func_name, file_hint = _parse_reference(content)
    if not func_name:
        return line

    current_key = current_doc.resolve()
    preferred_variant = index.variant_by_doc.get(current_key)
    if scope == RELATION_SAME:
        anchor = index.by_doc.get(current_key, {}).get(func_name)
        if not anchor:
            return line
        return _format_link(indent, display, anchor, None, current_key, _line_ending(line))

    file_key = None
    if file_hint:
        file_key = file_hint.lower()
        if not file_key.endswith(".go"):
            file_key = f"{file_key}.go"
    target_doc = index.find_doc_for_go_file(file_key, preferred_variant) if file_key else None
    if target_doc is None:
        target_doc = index.find_unique_doc_for_func(func_name, current_key, preferred_variant)
    if target_doc is None:
        return line
    anchor = index.by_doc.get(target_doc, {}).get(func_name)
    if not anchor:
        return line
    return _format_link(indent, display, anchor, target_doc, current_key, _line_ending(line))


def _line_ending(line: str) -> str:
    return "\n" if line.endswith("\n") else ""


def _add_links(lines: List[str], index: DocIndex, doc_path: Path) -> List[str]:
    output: List[str] = []
    relation_scope: Optional[str] = None
    relation_indent = 0
    label_indent: Optional[int] = None
    sublist_indent: Optional[int] = None
    in_code_block = False

    for line in lines:
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        if stripped.startswith("```"):
            in_code_block = not in_code_block
            output.append(line)
            continue
        if in_code_block:
            output.append(line)
            continue

        if relation_scope and stripped and indent <= relation_indent:
            relation_scope = None
            label_indent = None
            sublist_indent = None

        scope = _detect_relation_scope(line)
        if scope:
            relation_scope = scope
            relation_indent = indent
            label_indent = None
            sublist_indent = None
            output.append(line)
            continue

        if relation_scope:
            if stripped.startswith("-") and stripped[1:].strip().endswith(":"):
                label_indent = indent
                sublist_indent = indent + 4
                output.append(line)
                continue
            if sublist_indent is not None and label_indent is not None:
                if stripped.startswith("-") and indent == label_indent:
                    line = f"{' ' * sublist_indent}{stripped}{_line_ending(line)}"
            output.append(_maybe_link_item(line, relation_scope, index, doc_path))
        else:
            output.append(line)

    return output


def _collect_targets(target: Path) -> List[Path]:
    if target.is_dir():
        return sorted(target.rglob("*.doc.md"))
    return [target]


def _write_or_print(path: Path, lines: List[str], in_place: bool, out_path: Optional[Path]) -> None:
    content = "".join(lines)
    if out_path:
        out_path.write_text(content, encoding="utf-8")
    elif in_place:
        path.write_text(content, encoding="utf-8")
    else:
        sys.stdout.write(content)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Add markdown cross-links to generated and filled Go documentation.",
    )
    parser.add_argument("target", type=Path, help="*.doc.md file or directory with docs")
    parser.add_argument(
        "--docs-root",
        type=Path,
        default=None,
        help="Root directory to scan for *.doc.md files (default: target dir or parent).",
    )
    parser.add_argument("--in-place", action="store_true", help="Rewrite files in place.")
    parser.add_argument("--out", type=Path, default=None, help="Output file (single target only).")
    args = parser.parse_args(argv)

    target = args.target
    if not target.exists():
        print(f"error: target '{target}' not found", file=sys.stderr)
        return 1

    targets = _collect_targets(target)
    if not targets:
        print("error: no .doc.md files found", file=sys.stderr)
        return 1

    if args.out and len(targets) != 1:
        print("error: --out requires a single target file", file=sys.stderr)
        return 1
    if target.is_dir() and not args.in_place:
        print("error: directory target requires --in-place", file=sys.stderr)
        return 1

    docs_root = args.docs_root
    if docs_root is None:
        docs_root = target if target.is_dir() else target.parent
    doc_files = _collect_doc_files(docs_root)
    for target_path in targets:
        resolved = target_path.resolve()
        if resolved not in doc_files:
            doc_files.append(resolved)
    doc_files = sorted(doc_files)
    index = _build_index(doc_files)

    for doc_path in targets:
        lines = doc_path.read_text(encoding="utf-8").splitlines(keepends=True)
        updated = _add_links(lines, index, doc_path.resolve())
        _write_or_print(doc_path, updated, args.in_place, args.out)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
