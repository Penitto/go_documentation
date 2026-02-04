from __future__ import annotations

import unittest
from pathlib import Path

from go_template.generator import _prepare_render_inputs


class TestReadWriteSelectors(unittest.TestCase):
    def test_nested_selectors_included(self) -> None:
        go_file = Path("demo/nested.go")
        (
            _resolved_path,
            _types,
            _type_details,
            _consts,
            _vars,
            funcs,
            _internal_imports,
            _other_callers,
        ) = _prepare_render_inputs(go_file)

        funcs_by_name = {func["name"]: func for func in funcs}
        build = funcs_by_name["BuildTask"]
        update = funcs_by_name["UpdateTask"]
        read = funcs_by_name["ReadScore"]

        self.assertIn("task.Payload.Info.Metrics.Score", build["write_vars"])
        self.assertIn("defaultScore", build["read_vars"])

        self.assertIn("task.Payload.Info.Metrics.Score", update["write_vars"])
        self.assertIn("task.Meta.Flags.Active", update["write_vars"])
        self.assertIn("task.Payload.Info.Metrics.Score", update["read_vars"])

        self.assertIn("task.Payload.Info.Metrics.Score", read["read_vars"])


if __name__ == "__main__":
    raise SystemExit(unittest.main())
