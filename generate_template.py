#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from go_template import generate_documentation


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a documentation template for a Go file.")
    parser.add_argument("go_file", type=Path, help="Path to the .go file")
    parser.add_argument(
        "--out",
        type=Path,
        help="Destination Markdown file. Defaults to <go_file>.md in the same directory.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    go_file: Path = args.go_file
    output_path: Path | None = args.out

    try:
        generated_path = generate_documentation(go_file, output_path)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(generated_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
