"""
tests/test_frontend_xss.py — output-encoding regression guard for the web UI
============================================================================
The backend stores clinical text verbatim; escaping user-controlled data is a
*render-time* concern (see test_storage_and_metadata_safety.py). So the whole
burden of stopping DOM/stored XSS sits in the frontend: every untrusted value
that reaches an HTML template must pass through `escapeHtml()`.

History (see docs/DOM_XSS_SELF_AUDIT.md):
  * Round 1 found a reflected XSS in the command-palette search box. The guard
    written then was a DENYLIST of three exact strings (`${query}`,
    `${e.message}`, `${err.message}`).
  * Round 2 found four more sinks that denylist could never catch: the
    decrypted-record view, the attachment view (file name / type / data), and
    `${e.message || 'Unknown error'}` on the dashboard — the same error text,
    written slightly differently.

This guard is therefore a RULE, not a list of known bugs: any `${...}` that
reads from an untrusted source (error text, a record/user/log field, a file
name, user input) must be wrapped in escapeHtml(). The few expressions that
touch such a source but are provably safe (numbers, ternaries that only yield
constants, a localStorage key) are listed in REVIEWED_SAFE, each one checked
by hand. A new raw interpolation fails CI until it is escaped or reviewed.

Limits, stated honestly: this is a static, regex-level check. It inspects the
innermost `${...}` (no braces or backticks inside), skips lines that are not
HTML sinks (textContent, URLs, notifications, console), and cannot follow data
through variables — which is why values are escaped where they are built
(e.g. `const name = escapeHtml(fileName)`), so the variable itself is safe.
"""

import re
import unittest
from pathlib import Path

JS_ROOT = Path(__file__).resolve().parent.parent / "backend" / "static" / "js"

# Innermost interpolations: no braces or backticks inside the ${...}.
INTERPOLATION = re.compile(r"\$\{([^{}`]*)\}")

# Where untrusted data comes from.
UNTRUSTED = re.compile(
    r"\b(e|err|ex)\.message\b"                      # error text may echo input
    r"|\b(r|d|rec|log|u|c|n|item|acc|token|clinical|data|res|b)\."
    r"(?!(block_index|index|seq|is_valid|chain_length|length|broken_at|timestamp)\b)"
    r"[A-Za-z_]+"                                   # record / user / log fields
    r"|\b(fileName|fileType|fileData|query|msg|reason|doctor|username)\b"
)

# Lines that do not write HTML (safe sinks or not a sink at all), or carry an
# explicit `// xss-reviewed: <reason>` marker — like `# noqa`, a reviewed,
# justified exception that stays visible in the code.
NOT_AN_HTML_SINK = re.compile(
    r"xss-reviewed:|textContent|addNotification\(|console\.|alert\(|confirm\("
    r"|apiFetch\(|fetch\(|url\s*[+]?=|\.value\s*="
)

# Touch an untrusted name but cannot carry markup. Each one reviewed by hand.
REVIEWED_SAFE = {
    # ternaries that only ever yield constant strings
    "u.role==='admin'?'admin':'practitioner'",
    "d.integrity.count === 1 ? 'y' : 'ies'",
    "item.type === 'nav' ? '🧭' : item.type === 'action' ? '⚡' : '📄'",
    "n.read ? 'color: var(--muted);' : 'font-weight: 500;'",
    "r.is_protected?'is-encrypted':''",
    "r.is_correction?'is-correction':''",
    # numbers from the server
    "d.integrity.count",
    "d.integrity.broken_at",
    # a formatted timestamp
    "r.correction.corrected_at ? ' on ' + new Date(r.correction.corrected_at*1000)"
    ".toLocaleString('en-GB') : ''",
    # command-palette shortcut: a constant, or "#" + a block number
    "item.shortcut",
    # a localStorage key, never written into HTML
    "currentUser ? currentUser.username : 'guest'",
}


def _js_files():
    return sorted(p for p in JS_ROOT.rglob("*.js") if "vendor" not in p.parts)


def find_unescaped_untrusted():
    offenders = []
    for path in _js_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if NOT_AN_HTML_SINK.search(line):
                continue
            for expr in INTERPOLATION.findall(line):
                expr = expr.strip()
                if expr.startswith("escapeHtml(") or expr in REVIEWED_SAFE:
                    continue
                if UNTRUSTED.search(expr):
                    offenders.append(f"{path.name}:{lineno}: ${{{expr}}}")
    return offenders


class TestFrontendOutputEncoding(unittest.TestCase):
    def test_js_tree_exists(self):
        self.assertTrue(JS_ROOT.is_dir(), f"frontend JS root not found: {JS_ROOT}")
        self.assertTrue(_js_files(), "no .js files found to scan")

    def test_every_untrusted_interpolation_is_escaped(self):
        offenders = find_unescaped_untrusted()
        self.assertFalse(
            offenders,
            "untrusted value interpolated into HTML without escapeHtml() "
            "(escape it, or review it and add it to REVIEWED_SAFE):\n" + "\n".join(offenders),
        )

    def test_rule_catches_the_bugs_it_was_written_for(self):
        """The rule must flag every sink found in rounds 1 and 2 — including the
        `|| 'Unknown error'` variant the old denylist missed."""
        for expr in ('query', 'e.message', "e.message || 'Unknown error'",
                     'd.notes', 'd.title || (r ? r.title : \'\')', 'fileName',
                     'fileType', 'fileData', 'msg'):
            self.assertTrue(UNTRUSTED.search(expr), f"rule misses ${{{expr}}}")

    def test_search_reflection_is_escaped(self):
        """Round 1: the command-palette 'No results' branch is encoded."""
        app_js = (JS_ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn("No results found for", app_js)
        self.assertIn("${escapeHtml(query)}", app_js)

    def test_escapehtml_neutralises_html_metacharacters(self):
        """escapeHtml() must encode all five HTML-significant characters."""
        utils = (JS_ROOT / "modules" / "utils.js").read_text(encoding="utf-8")
        self.assertIn("export function escapeHtml", utils)
        for needle in ("&amp;", "&lt;", "&gt;", "&quot;", "&#039;"):
            self.assertIn(needle, utils, f"escapeHtml() does not emit {needle}")


if __name__ == "__main__":
    unittest.main()
