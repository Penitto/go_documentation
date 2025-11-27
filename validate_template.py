#!/usr/bin/env python3
"""Validate that a filled documentation template matches the expected structure."""

from __future__ import annotations

import argparse
import logging
import re
import sys
import tempfile
from pathlib import Path
from typing import List

try:
    from go_template.generator import generate_documentation
except ModuleNotFoundError:
    from go_documentation.go_template.generator import generate_documentation  # type: ignore

PLACEHOLDER_REGEX = re.compile(r"(—|<[^>]+>)")
MULTILINE_DETAIL_RE = re.compile(r"^\s{2,}[-*•] ")
CONTINUATION_RE = re.compile(r"^\s+\S.*$")


def generate_reference_template(go_file: Path) -> List[str]:
    """Create a fresh template for the provided Go file."""
    with tempfile.NamedTemporaryFile(suffix=".doc.md", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    root_logger = logging.getLogger()
    previous_level = root_logger.level
    try:
        # Silence informational logging emitted by generator.
        root_logger.setLevel(max(previous_level, logging.WARNING))
        generate_documentation(go_file, tmp_path)
    finally:
        root_logger.setLevel(previous_level)
    content = tmp_path.read_text(encoding="utf-8").splitlines()
    tmp_path.unlink(missing_ok=True)
    return content


def is_placeholder_value_valid(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    if stripped == "—":
        return False
    if stripped.lower() == "<нет>":
        return True
    if "<" in stripped and ">" in stripped:
        return False
    return True


def _strip_backticks(value: str) -> str:
    return value.replace("`", "")


def compare_lines(template_line: str, actual_line: str, line_no: int) -> list[str]:
    """Compare a single line, returning a list of issues."""
    issues: List[str] = []
    template_clean = _strip_backticks(template_line)
    actual_clean = _strip_backticks(actual_line)
    matches = list(PLACEHOLDER_REGEX.finditer(template_clean))
    if not matches:
        if template_clean != actual_clean:
            issues.append(
                f"Line {line_no}: expected '{template_line}' but found '{actual_line}'"
            )
        return issues

    cursor = 0
    last_end = 0
    for idx, match in enumerate(matches):
        segment = template_clean[last_end:match.start()]
        if not actual_clean.startswith(segment, cursor):
            issues.append(
                f"Line {line_no}: expected segment '{segment}' before placeholder"
            )
            return issues
        cursor += len(segment)
        placeholder = match.group()
        trailing_text = template_clean[match.end():].strip()
        if placeholder.lower() == "<нет>" or (
            placeholder == "—" and trailing_text
        ):
            literal = placeholder if placeholder != "—" else "—"
            if not actual_clean.startswith(literal, cursor):
                issues.append(
                    f"Line {line_no}: expected literal '{literal}'"
                )
                return issues
            cursor += len(literal)
            last_end = match.end()
            continue
        if placeholder == "—" and not trailing_text:
            value = actual_clean[cursor:]
            cursor = len(actual_clean)
            if not is_placeholder_value_valid(value):
                issues.append(
                    f"Line {line_no}: placeholder '{placeholder}' not replaced with meaningful content"
                )
                return issues
            last_end = match.end()
            continue
        next_start = matches[idx + 1].start() if idx + 1 < len(matches) else len(
            template_clean
        )
        next_segment = template_clean[match.end():next_start]
        if next_segment:
            next_pos = actual_clean.find(next_segment, cursor)
            if next_pos == -1:
                issues.append(
                    f"Line {line_no}: expected segment '{next_segment}' after placeholder '{placeholder}'"
                )
                return issues
            value = actual_clean[cursor:next_pos]
            cursor = next_pos
        else:
            value = actual_clean[cursor:]
            cursor = len(actual_clean)
        if not is_placeholder_value_valid(value):
            issues.append(
                f"Line {line_no}: placeholder '{placeholder}' not replaced with meaningful content"
            )
            return issues
        last_end = match.end()

    remaining = template_clean[last_end:]
    if not actual_clean.startswith(remaining, cursor):
        issues.append(
            f"Line {line_no}: expected trailing segment '{remaining}'"
        )
        return issues
    cursor += len(remaining)
    if cursor != len(actual_clean):
        issues.append(
            f"Line {line_no}: unexpected extra content after '{remaining}'"
        )
    return issues


def validate_document(go_file: Path, doc_file: Path) -> list[str]:
    """Validate a filled template and return a list of discovered issues."""
    issues: List[str] = []
    template_lines = generate_reference_template(go_file)
    try:
        doc_lines = doc_file.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return [f"Documentation file '{doc_file}' does not exist"]

    t_idx = 0
    d_idx = 0
    while t_idx < len(template_lines) and d_idx < len(doc_lines):
        while t_idx < len(template_lines) and template_lines[t_idx].strip() == "":
            t_idx += 1
        while d_idx < len(doc_lines) and doc_lines[d_idx].strip() == "":
            d_idx += 1
        if t_idx >= len(template_lines) or d_idx >= len(doc_lines):
            break
        # Skip extra empty lines in the document if the template expects content.
        while (
            d_idx < len(doc_lines)
            and doc_lines[d_idx].strip() == ""
            and template_lines[t_idx].strip() != ""
        ):
            d_idx += 1
        # Align paired empty lines.
        if (
            t_idx < len(template_lines)
            and template_lines[t_idx].strip() == ""
            and d_idx < len(doc_lines)
            and doc_lines[d_idx].strip() == ""
        ):
            t_idx += 1
            d_idx += 1
            continue

        tpl_line = template_lines[t_idx]
        doc_line = doc_lines[d_idx]
        issues.extend(compare_lines(tpl_line, doc_line, d_idx + 1))
        t_idx += 1
        d_idx += 1
        if tpl_line.startswith("- Внутренняя логика:"):
            while d_idx < len(doc_lines) and MULTILINE_DETAIL_RE.match(doc_lines[d_idx]):
                d_idx += 1
        if PLACEHOLDER_REGEX.search(tpl_line):
            while d_idx < len(doc_lines) and CONTINUATION_RE.match(doc_lines[d_idx]):
                d_idx += 1
            while d_idx < len(doc_lines):
                stripped = doc_lines[d_idx].lstrip()
                if stripped.startswith("-") or stripped.startswith("#"):
                    break
                d_idx += 1

    if t_idx < len(template_lines):
        for missing_idx in range(t_idx, len(template_lines)):
            issues.append(
                f"Line {missing_idx + 1}: expected '{template_lines[missing_idx]}'"
            )
    while d_idx < len(doc_lines):
        issues.append(f"Line {d_idx + 1}: unexpected extra content")
        d_idx += 1

    return issues


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a filled Go documentation template."
    )
    parser.add_argument(
        "go_file",
        type=Path,
        help="Path to the original .go file used to generate the template",
    )
    parser.add_argument(
        "doc_file",
        type=Path,
        help="Path to the completed documentation file",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    issues = validate_document(args.go_file, args.doc_file)
    if issues:
        print("Template validation failed:", file=sys.stderr)
        for msg in issues:
            print(f"- {msg}", file=sys.stderr)
        return 1
    print("Template validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
