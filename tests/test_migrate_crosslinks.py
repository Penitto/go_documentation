from __future__ import annotations

import unittest

from migrate_crosslinks import _rewrite_links


class TestMigrateCrosslinks(unittest.TestCase):
    def test_rewrites_local_anchor(self) -> None:
        content = "- [normalizePayload](#func-normalizepayload)\n"
        updated, changed = _rewrite_links(content)
        self.assertEqual(changed, 1)
        self.assertIn(
            "(#markdown-header-func-normalizepayload)",
            updated,
        )

    def test_rewrites_cross_file_anchor(self) -> None:
        content = "- [BuildBatch (worker.go)](worker.doc.md#func-buildbatch)\n"
        updated, changed = _rewrite_links(content)
        self.assertEqual(changed, 1)
        self.assertIn(
            "(worker.doc.md#markdown-header-func-buildbatch)",
            updated,
        )

    def test_does_not_touch_new_anchor(self) -> None:
        content = "- [normalizePayload](#markdown-header-func-normalizepayload)\n"
        updated, changed = _rewrite_links(content)
        self.assertEqual(changed, 0)
        self.assertEqual(updated, content)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
