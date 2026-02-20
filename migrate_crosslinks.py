#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable, List, Tuple


LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
LABEL_WITH_FILE_RE = re.compile(r"^(?P<name>.+?)\s*\((?P<file>[^)]+)\)$")
ANCHOR_STYLE_BITBUCKET = "bitbucket"
ANCHOR_STYLE_COMMONMARK = "commonmark"


def _collect_targets(target: Path) -> List[Path]:
    if target.is_dir():
        return sorted(
            path
            for path in target.rglob("*")
            if path.is_file() and path.suffix.lower() == ".md"
        )
    return [target]


def _slugify_bitbucket_anchor(text: str) -> str:
    text = text.replace("`", "").strip().lower()
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^a-z0-9-]", "", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "func"


def _slugify_common_anchor(text: str) -> str:
    text = text.replace("`", "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "func"


def _anchor_fragment(name: str, style: str) -> str:
    slug = (
        _slugify_bitbucket_anchor(name)
        if style == ANCHOR_STYLE_BITBUCKET
        else _slugify_common_anchor(name)
    )
    if style == ANCHOR_STYLE_BITBUCKET:
        return f"markdown-header-func-{slug}"
    return f"func-{slug}"


def _normalize_relation_target_name(name: str) -> str:
    name = name.strip()
    if "." not in name:
        return name
    left, right = name.split(".", 1)
    if left and left[0].islower():
        return right
    return name


def _extract_target_name_from_label(label: str) -> str:
    label = label.strip()
    match = LABEL_WITH_FILE_RE.match(label)
    if match:
        return _normalize_relation_target_name(match.group("name").strip())
    if ":" in label:
        left, right = label.split(":", 1)
        left = left.strip()
        if left.endswith(".go") or "/" in left:
            return _normalize_relation_target_name(right.strip())
    return _normalize_relation_target_name(label)


def _rewrite_anchor_target(target: str, label: str, style: str) -> Tuple[str, int]:
    hash_idx = target.rfind("#")
    if hash_idx == -1:
        return target, 0
    fragment = target[hash_idx + 1 :]
    if not (
        fragment.startswith("func-")
        or fragment.startswith("markdown-header-func-")
    ):
        return target, 0

    prefix = target[:hash_idx]
    target_name = _extract_target_name_from_label(label)
    updated_fragment = _anchor_fragment(target_name, style)
    updated_target = f"{prefix}#{updated_fragment}"
    if updated_target == target:
        return target, 0
    return updated_target, 1


def _rewrite_links(content: str, style: str) -> Tuple[str, int]:
    changed = 0

    def _replace_link(match: re.Match[str]) -> str:
        nonlocal changed
        label, target = match.group(1), match.group(2)
        updated_target, count = _rewrite_anchor_target(target, label, style)
        if count:
            changed += count
            return f"[{label}]({updated_target})"
        return match.group(0)

    return LINK_RE.sub(_replace_link, content), changed


def _migrate_file(path: Path, in_place: bool, out_path: Path | None, style: str) -> int:
    original = path.read_text(encoding="utf-8")
    updated, changed = _rewrite_links(original, style)
    if in_place:
        path.write_text(updated, encoding="utf-8")
    elif out_path:
        out_path.write_text(updated, encoding="utf-8")
    else:
        sys.stdout.write(updated)
    return changed


def _iter_targets(targets: Iterable[Path]) -> Iterable[Path]:
    for path in targets:
        if path.is_file():
            yield path


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Migrate function anchors in markdown links. "
            "Use --anchor-style bitbucket for #markdown-header-func-* "
            "or --anchor-style commonmark for #func-*."
        ),
    )
    parser.add_argument("target", type=Path, help="Markdown file or directory")
    parser.add_argument("--in-place", action="store_true", help="Rewrite files in place")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write migrated content to a new file (single-file target only)",
    )
    parser.add_argument(
        "--anchor-style",
        choices=[ANCHOR_STYLE_BITBUCKET, ANCHOR_STYLE_COMMONMARK],
        default=ANCHOR_STYLE_BITBUCKET,
        help="Target anchor style (default: bitbucket)",
    )
    args = parser.parse_args(argv)

    target: Path = args.target
    if not target.exists():
        print(f"error: target '{target}' not found", file=sys.stderr)
        return 1

    targets = list(_iter_targets(_collect_targets(target)))
    if not targets:
        print("error: no markdown files found", file=sys.stderr)
        return 1

    if args.out and len(targets) != 1:
        print("error: --out can be used only with a single file target", file=sys.stderr)
        return 1
    if target.is_dir() and not args.in_place:
        print("error: directory target requires --in-place", file=sys.stderr)
        return 1

    total_changed = 0
    for path in targets:
        changed = _migrate_file(path, args.in_place, args.out, args.anchor_style)
        total_changed += changed
        print(f"{path}: updated {changed} link(s)", file=sys.stderr)

    print(f"total updated links: {total_changed}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
