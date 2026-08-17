#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"

out=${1:-target/interoperability}
mkdir -p "$out"

cases=(
  "khmer|/usr/share/fonts/truetype/noto/NotoSansKhmer-Regular.ttf"
  "arabic|/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf"
  "devanagari|/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf"
  "mixed-bidi|/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
)

export UNICODE_PDF_REQUIRE_SYSTEM_HARFBUZZ=1
export UNICODE_PDF_REQUIRE_SYSTEM_FRIBIDI=1
cargo test -q -p unicode-pdf-shape-harfbuzz -p unicode-pdf-bidi-fribidi

for entry in "${cases[@]}"; do
  name=${entry%%|*}
  font=${entry#*|}
  if [[ ! -f "$font" ]]; then
    printf 'skip %-12s missing %s\n' "$name" "$font"
    continue
  fi

  pdf="$out/$name.pdf"
  txt="$out/$name.poppler.txt"
  cargo run -q -p unicode-pdf-cli -- emit-pdf "$font" "fixtures/$name.txt" "$pdf"

  if command -v gs >/dev/null 2>&1; then
    gs -q -dNOPAUSE -dBATCH -sDEVICE=nullpage "$pdf"
  fi

  tagged=unknown
  if command -v pdfinfo >/dev/null 2>&1; then
    tagged=$(pdfinfo "$pdf" | awk '/^Tagged:/ {print $2}')
  fi

  if command -v pdftotext >/dev/null 2>&1; then
    pdftotext -enc UTF-8 "$pdf" "$txt"
    python3 - "$name" "$txt" "$tagged" <<'PY'
from pathlib import Path
import sys
name, extracted, tagged = sys.argv[1:]
source = Path(f"fixtures/{name}.txt").read_text(encoding="utf-8")
actual = Path(extracted).read_text(encoding="utf-8")
for control in ("\u202a", "\u202b", "\u202c", "\u2066", "\u2067", "\u2069", "\f"):
    actual = actual.replace(control, "")
actual = actual.rstrip("\n") + "\n"
print(f"{name:12} tagged={tagged:3} poppler_exact={actual == source}")
PY
  fi
done
