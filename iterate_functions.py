#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import re

FUNC_HEADER_PATTERN = re.compile(r"^### `func\s+(.+?)`$")
SECTION_TITLES = {"Назначение файла", "Внутренняя структура"}


@dataclass
class FunctionBlock:
    name: str
    start_line: int
    end_line: int
    length: int
    kind: str = "func"  # func | section


@dataclass
class IteratorState:
    after_line: Optional[int] = None
    after_func: Optional[str] = None

    @classmethod
    def load(cls, path: Path) -> "IteratorState":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(after_line=data.get("after_line"), after_func=data.get("after_func"))
        except Exception:
            return cls()

    def dump(self, path: Path) -> None:
        payload = {"after_line": self.after_line, "after_func": self.after_func}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _collect_block_starts(lines: List[str]) -> List[FunctionBlock]:
    """Collect start positions for sections + functions without end boundaries yet."""
    starts: List[FunctionBlock] = []
    for idx, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        if stripped.startswith("## "):
            title = stripped[2:].strip()
            if title in SECTION_TITLES:
                starts.append(
                    FunctionBlock(
                        name=title,
                        start_line=idx,
                        end_line=len(lines),
                        length=0,
                        kind="section",
                    )
                )
        elif stripped.startswith("### "):
            match = FUNC_HEADER_PATTERN.match(stripped)
            if match:
                starts.append(
                    FunctionBlock(
                        name=match.group(1).strip(),
                        start_line=idx,
                        end_line=len(lines),
                        length=0,
                        kind="func",
                    )
                )
    return sorted(starts, key=lambda b: b.start_line)


def parse_function_blocks(lines: List[str]) -> List[FunctionBlock]:
    """Return blocks (sections + functions) with computed end/length."""
    starts = _collect_block_starts(lines)
    blocks: List[FunctionBlock] = []
    for i, block in enumerate(starts):
        end_line = len(lines) if i == len(starts) - 1 else starts[i + 1].start_line - 1
        block.end_line = end_line
        block.length = block.end_line - block.start_line + 1
        blocks.append(block)
    return blocks


def select_next_block(
    blocks: List[FunctionBlock],
    after_line: Optional[int],
    after_func: Optional[str],
) -> Optional[FunctionBlock]:
    """Pick the next function block based on either a line cursor or function name."""
    start_index = 0
    if after_func:
        for i, block in enumerate(blocks):
            if block.name == after_func:
                start_index = i + 1
                break
    if after_line is not None:
        for block in blocks[start_index:]:
            if block.start_line > after_line:
                return block
        return None
    return blocks[start_index] if start_index < len(blocks) else None


def next_function_segment(
    template: Path,
    state: Optional[IteratorState] = None,
    state_file: Optional[Path] = None,
) -> Tuple[Optional[FunctionBlock], IteratorState]:
    """Return next function block with current line numbers, resilient to edits.

    Алгоритм под ваш процесс:
      1. Вызываете next_function_segment -> получаете start_line и length.
      2. Передаёте этот диапазон LLM, она правит файл.
      3. Повторяете вызов next_function_segment: файл перечитывается, поэтому
         сдвиги учтутся.
    """
    if state is None:
        state = IteratorState()
    if state_file and (state.after_line is None and state.after_func is None):
        state = IteratorState.load(state_file)

    lines = template.read_text(encoding="utf-8").splitlines()
    blocks = parse_function_blocks(lines)
    block = select_next_block(blocks, state.after_line, state.after_func)

    if block:
        state.after_line = block.end_line
        state.after_func = block.name
        if state_file:
            state.dump(state_file)

    return block, state


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Iterate over functions in a documentation template, resilient to ongoing edits."
    )
    parser.add_argument("template", type=Path, help="Path to the generated *.doc.md file")
    parser.add_argument(
        "--after-line",
        type=int,
        default=None,
        help="Start search after this line number (1-based).",
    )
    parser.add_argument(
        "--after-func",
        type=str,
        default=None,
        help="Start search after this function name (as shown in the header).",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=None,
        help="Optional JSON state file; stores cursor so you can call the script repeatedly.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    if not args.template.is_file():
        print(f"error: template file '{args.template}' not found", file=sys.stderr)
        return 1

    init_state = IteratorState(after_line=args.after_line, after_func=args.after_func)
    next_block, final_state = next_function_segment(args.template, init_state, args.state_file)

    if next_block is None:
        if args.json:
            print(json.dumps({"done": True}, ensure_ascii=False))
        else:
            print("Все функции пройдены или не найдены.")
        return 0

    if args.json:
        print(
            json.dumps(
                {
                    "done": False,
                    "block": asdict(next_block),
                    "state": asdict(final_state),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"Следующая функция: {next_block.name}")
        print(f"Стартовая строка: {next_block.start_line}")
        print(f"Количество строк: {next_block.length}")
        print(f"Конечная строка: {next_block.end_line}")
        if args.state_file:
            print(f"Состояние сохранено в {args.state_file}")
        else:
            print(
                "Подсказка: передайте --after-line {line} на следующем вызове".format(
                    line=next_block.end_line
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
