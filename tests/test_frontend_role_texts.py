"""
tests/test_frontend_role_texts.py — every role-aware text has a wording
======================================================================
Elements marked data-role-text="<key>" get their text from ROLE_TEXTS in
backend/static/js/modules/utils.js, per role. A key used in the page but
missing from the table (or without a `default`) would leave some role looking
at a raw key or at another role's sentence.
"""

import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "backend" / "static"


def _role_text_table() -> dict:
    """{key: set of roles} parsed from the ROLE_TEXTS object literal."""
    src = (STATIC / "js" / "modules" / "utils.js").read_text(encoding="utf-8")
    block = re.search(r"export const ROLE_TEXTS = \{(.*?)\n\};", src, re.S)
    assert block, "ROLE_TEXTS not found in utils.js"
    table = {}
    for key, body in re.findall(r"'([a-z-]+)':\s*\{(.*?)\n  \}", block.group(1), re.S):
        table[key] = set(re.findall(r"^\s*([a-z_]+):", body, re.M))
    return table


class TestRoleTexts(unittest.TestCase):
    def test_table_parses(self):
        self.assertIn("clients-sub", _role_text_table())

    def test_every_key_used_in_the_page_is_defined(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        used = set(re.findall(r'data-role-text="([^"]+)"', html))
        self.assertTrue(used)
        missing = used - set(_role_text_table())
        self.assertFalse(missing, f"data-role-text keys without a wording: {sorted(missing)}")

    def test_every_key_has_a_default(self):
        for key, roles in _role_text_table().items():
            with self.subTest(key=key):
                self.assertIn("default", roles)


if __name__ == "__main__":
    unittest.main()
