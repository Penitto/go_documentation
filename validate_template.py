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

from go_template.generator import generate_documentation

PLACEHOLDER_REGEX = re.compile(r"(—|<[^>]+>)")


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
    if "<" in stripped and ">" in stripped:
        return False
    return True


def compare_lines(template_line: str, actual_line: str, line_no: int) -> list[str]:
    """Compare a single line, returning a list of issues."""
    issues: List[str] = []
    matches = list(PLACEHOLDER_REGEX.finditer(template_line))
    if not matches:
        if template_line != actual_line:
            issues.append(
                f"Line {line_no}: expected '{template_line}' but found '{actual_line}'"
            )
        return issues

    cursor = 0
    last_end = 0
    for idx, match in enumerate(matches):
        segment = template_line[last_end:match.start()]
        if not actual_line.startswith(segment, cursor):
            issues.append(
                f"Line {line_no}: expected segment '{segment}' before placeholder"
            )
            return issues
        cursor += len(segment)
        placeholder = match.group()
        next_start = matches[idx + 1].start() if idx + 1 < len(matches) else len(
            template_line
        )
        next_segment = template_line[match.end():next_start]
        if next_segment:
            next_pos = actual_line.find(next_segment, cursor)
            if next_pos == -1:
                issues.append(
                    f"Line {line_no}: expected segment '{next_segment}' after placeholder '{placeholder}'"
                )
                return issues
            value = actual_line[cursor:next_pos]
            cursor = next_pos
        else:
            value = actual_line[cursor:]
            cursor = len(actual_line)
        if not is_placeholder_value_valid(value):
            issues.append(
                f"Line {line_no}: placeholder '{placeholder}' not replaced with meaningful content"
            )
            return issues
        last_end = match.end()

    remaining = template_line[last_end:]
    if not actual_line.startswith(remaining, cursor):
        issues.append(
            f"Line {line_no}: expected trailing segment '{remaining}'"
        )
        return issues
    cursor += len(remaining)
    if cursor != len(actual_line):
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

    if len(template_lines) != len(doc_lines):
        issues.append(
            f"Line count mismatch: expected {len(template_lines)} lines, found {len(doc_lines)}"
        )
    limit = min(len(template_lines), len(doc_lines))
    for idx in range(limit):
        issues.extend(compare_lines(template_lines[idx], doc_lines[idx], idx + 1))

    if len(doc_lines) > len(template_lines):
        for extra_idx in range(len(template_lines) + 1, len(doc_lines) + 1):
            issues.append(f"Line {extra_idx}: unexpected extra content")

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
