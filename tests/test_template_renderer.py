from __future__ import annotations

import unittest
from pathlib import Path

from go_template.template_renderer import render_template_blocks


class TestTemplateRenderer(unittest.TestCase):
    def test_heading_levels_and_placeholders(self) -> None:
        blocks = render_template_blocks(
            Path("demo/nested.go"),
            types=[],
            type_details={},
            consts=[],
            vars_=[],
            funcs=[],
            internal_imports=[],
            file_callers=[],
        )
        lines = [line for block in blocks for line in block]

        self.assertEqual(lines[0], "# Назначение файла")
        self.assertIn("<<FILL 1>>", lines[1])
        self.assertIn("# Внутренняя структура", lines)
        self.assertIn("## Функции и методы", lines)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
