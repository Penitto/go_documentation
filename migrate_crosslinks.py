#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable, List, Tuple


LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
OLD_ANCHOR_RE = re.compile(r"#func-([a-z0-9][a-z0-9-]*)")


def _collect_targets(target: Path) -> List[Path]:
    if target.is_dir():
        return sorted(
            path
            for path in target.rglob("*")
            if path.is_file() and path.suffix.lower() == ".md"
        )
    return [target]


def _rewrite_links(content: str) -> Tuple[str, int]:
    changed = 0

    def _replace_link(match: re.Match[str]) -> str:
        nonlocal changed
        label, target = match.group(1), match.group(2)
        updated_target, count = OLD_ANCHOR_RE.subn(
            r"#markdown-header-func-\1",
            target,
        )
        if count:
            changed += count
            return f"[{label}]({updated_target})"
        return match.group(0)

    return LINK_RE.sub(_replace_link, content), changed


def _migrate_file(path: Path, in_place: bool, out_path: Path | None) -> int:
    original = path.read_text(encoding="utf-8")
    updated, changed = _rewrite_links(original)
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
        description="Migrate old #func-* markdown anchors to #markdown-header-func-*.",
    )
    parser.add_argument("target", type=Path, help="Markdown file or directory")
    parser.add_argument("--in-place", action="store_true", help="Rewrite files in place")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write migrated content to a new file (single-file target only)",
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
        changed = _migrate_file(path, args.in_place, args.out)
        total_changed += changed
        print(f"{path}: updated {changed} link(s)", file=sys.stderr)

    print(f"total updated links: {total_changed}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
