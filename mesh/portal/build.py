#!/usr/bin/env python3
"""build.py — inline index.html into worker.js for single-file CF deploy.

wrangler doesn't natively bundle .html as a string at deploy time
(text_blobs is opt-in and finicky on legacy syntax). So we substitute
the HTML inside worker.js as a JS template literal before
`wrangler deploy`. Re-run any time you edit index.html.

Output: dist/worker.js — what wrangler ships.
"""
from __future__ import annotations
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC_HTML = HERE / "index.html"
SRC_SKILL = HERE / "skill" / "SKILL.md"
SRC_WORKER = HERE / "worker.js"
OUT_WORKER = HERE / "dist" / "worker.js"
HTML_PLACEHOLDER = "INLINE_HTML"
SKILL_PLACEHOLDER = "INLINE_SKILL"


def escape_for_template_literal(s: str) -> str:
    """Escape a string for safe embedding inside a JS template literal.
    Order matters: backslash MUST come first (otherwise we'd double-
    escape our own escape sequences)."""
    return (s.replace("\\", "\\\\")
             .replace("`", "\\`")
             .replace("${", "\\${"))


def main() -> int:
    for f in (SRC_HTML, SRC_SKILL, SRC_WORKER):
        if not f.is_file():
            print(f"[FAIL] missing {f}", file=sys.stderr)
            return 1

    html = SRC_HTML.read_text(encoding="utf-8")
    skill = SRC_SKILL.read_text(encoding="utf-8")
    worker = SRC_WORKER.read_text(encoding="utf-8")

    for placeholder in (HTML_PLACEHOLDER, SKILL_PLACEHOLDER):
        if placeholder not in worker:
            print(f"[FAIL] {SRC_WORKER} has no {placeholder} placeholder to fill", file=sys.stderr)
            return 1

    # Defensive: replace only FIRST occurrence to avoid double-substitution
    # if someone re-runs against an already-built file.
    out = worker.replace(
        HTML_PLACEHOLDER, "`" + escape_for_template_literal(html) + "`", 1,
    ).replace(
        SKILL_PLACEHOLDER, "`" + escape_for_template_literal(skill) + "`", 1,
    )

    OUT_WORKER.parent.mkdir(parents=True, exist_ok=True)
    OUT_WORKER.write_text(out, encoding="utf-8")
    print(f"[OK] built {OUT_WORKER} ({len(out):,} bytes; "
          f"html={len(html):,}B, skill={len(skill):,}B)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
