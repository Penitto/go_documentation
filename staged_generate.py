#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path
from typing import List

from go_template import generate_documentation_iter


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a Go documentation template block-by-block, writing each block into its own file, then concatenating them."
    )
    parser.add_argument("go_file", type=Path, help="Path to the source .go file.")
    parser.add_argument(
        "--blocks-dir",
        type=Path,
        help="Directory to store intermediate block files (default: <go_file>-blocks beside the source).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Final combined Markdown file (default: <go_file>.doc.md).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"],
        help="Logging verbosity.",
    )
    parser.add_argument(
        "--keep-blocks",
        action="store_true",
        help="Keep intermediate block files after concatenation (default: remove).",
    )
    return parser.parse_args(argv)


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "block"


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(levelname)s: %(message)s",
    )

    go_file = args.go_file
    if not go_file.is_file():
        print(f"error: {go_file} is not a file", file=sys.stderr)
        return 1

    blocks_dir = args.blocks_dir or go_file.with_name(f"{go_file.stem}-blocks")
    final_path = args.out or go_file.with_suffix(".doc.md")

    blocks_dir.mkdir(parents=True, exist_ok=True)
    existing = list(blocks_dir.iterdir())
    if existing:
        print(f"error: blocks directory '{blocks_dir}' is not empty", file=sys.stderr)
        return 1

    block_paths: List[Path] = []

    def resolver(name: str, kind: str, index: int) -> Path:
        slug = _slugify(name or kind)
        filename = f"{index:03d}-{kind}-{slug}.md"
        target = blocks_dir / filename
        return target

    logging.info("Generating staged template for %s", go_file)
    for block in generate_documentation_iter(go_file, output_path=final_path, block_path_resolver=resolver):
        path = Path(block.path)
        block_paths.append(path)
        logging.debug(
            "Block %s (%s) -> %s [%d lines]",
            block.name,
            block.kind,
            path,
            block.length,
        )

    if not block_paths:
        print("warning: no blocks were generated", file=sys.stderr)

    logging.info("Concatenating %d blocks into %s", len(block_paths), final_path)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    with final_path.open("w", encoding="utf-8") as dst:
        for idx, block_path in enumerate(block_paths):
            content = block_path.read_text(encoding="utf-8")
            if idx > 0 and not content.startswith("\n"):
                dst.write("\n")
            dst.write(content.rstrip() + "\n")

    if not args.keep_blocks:
        for block_path in block_paths:
            try:
                block_path.unlink()
            except OSError:
                pass
        try:
            blocks_dir.rmdir()
        except OSError:
            pass

    logging.info("Template ready at %s", final_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
