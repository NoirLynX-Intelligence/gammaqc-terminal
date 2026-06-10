#!/usr/bin/env bash
# build.sh — inline index.html into worker.js for a single-file deploy.
#
# wrangler doesn't natively bundle .html as a string at deploy time
# (text_blobs is opt-in + finicky), so we just sed the literal into
# worker.js before `wrangler deploy`. Idempotent: re-run any time
# you edit index.html.
#
# Output: dist/worker.js — what wrangler ships.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC_HTML="$HERE/index.html"
SRC_WORKER="$HERE/worker.js"
OUT_DIR="$HERE/dist"
OUT_WORKER="$OUT_DIR/worker.js"

mkdir -p "$OUT_DIR"

# Escape the HTML for embedding inside a JS template literal:
#   backtick → \` , dollar → \$ , backslash → \\
ESCAPED=$(python3 - <<'PY' "$SRC_HTML"
import sys
html = open(sys.argv[1], encoding="utf-8").read()
# Order matters: backslash FIRST so we don't double-escape our own escapes
html = html.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
print(html)
PY
)

# Inject — replace the literal INLINE_HTML placeholder with the
# escaped template-literal form
python3 - <<PY
import re
worker = open(r"$SRC_WORKER", encoding="utf-8").read()
escaped = r"""$ESCAPED"""
# Replace `INLINE_HTML` with a backtick template literal
out = worker.replace("INLINE_HTML", "\`" + escaped + "\`", 1)
open(r"$OUT_WORKER", "w", encoding="utf-8").write(out)
print(f"✓ built $OUT_WORKER ({len(out)} bytes)")
PY
