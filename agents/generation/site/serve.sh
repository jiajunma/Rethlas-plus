#!/usr/bin/env bash
set -euo pipefail

SITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GEN_DIR="$(cd "$SITE_DIR/.." && pwd)"
CONTENT_DIR="$SITE_DIR/content"
RESULTS_DIR="$GEN_DIR/results"
TRANSFORM="$SITE_DIR/transform_math.py"
PORT="${PORT:-3264}"

if [[ ! -d "$SITE_DIR/themes/MATbook" ]]; then
  bash "$SITE_DIR/setup_theme.sh"
fi

sync_content() {
  echo "Syncing results into site/content/ ..."

  # Clean all generated chapters (everything except _index.md in content/)
  find "$CONTENT_DIR" -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} +

  if [[ ! -d "$RESULTS_DIR" ]]; then
    echo "No results directory found at $RESULTS_DIR"
    return
  fi

  local page_count=0
  local -A chapter_seen
  local chapter_weight=1

  while IFS= read -r -d '' md; do
    local rel="${md#"$RESULTS_DIR"/}"
    # rel is e.g. "my_problem/blueprint_verified.md"
    #          or "algebra/prob1/blueprint_verified.md"

    local stem
    stem="$(basename "$md" .md)"

    # Split into: first dir component = chapter, rest = flattened page name
    local chapter="${rel%%/*}"
    local rest="${rel#*/}"

    # Build page filename: flatten any intermediate dirs into the name
    local page_name
    if [[ "$rest" == */* ]]; then
      # Nested: algebra/prob1/blueprint.md → prob1_blueprint
      page_name="${rest//\//_}"
      page_name="${page_name%.md}"
    else
      # Flat: my_problem/blueprint.md → blueprint
      page_name="$stem"
    fi

    # Create chapter _index.md if not yet seen
    local chapter_dir="$CONTENT_DIR/$chapter"
    if [[ -z "${chapter_seen[$chapter]:-}" ]]; then
      mkdir -p "$chapter_dir"
      printf '+++\ntitle = "%s"\nsort_by = "weight"\nweight = %d\n+++\n' \
        "$chapter" "$chapter_weight" > "$chapter_dir/_index.md"
      chapter_seen[$chapter]=1
      chapter_weight=$((chapter_weight + 1))
    fi

    # Build page title
    local page_title
    if [[ "$rest" == */* ]]; then
      # e.g. "prob1 — blueprint_verified"
      local mid="${rest%/*}"
      page_title="${mid//\// — } — ${stem}"
    else
      page_title="$stem"
    fi

    local dest="$chapter_dir/${page_name}.md"
    local ts
    ts="$(date -r "$md" +%Y-%m-%d 2>/dev/null || echo "2026-01-01")"

    local tmp="$dest.tmp"
    python3 "$TRANSFORM" "$md" "$tmp"

    {
      printf '+++\ntitle = "%s"\ndate = %s\nweight = %d\n' \
        "$page_title" "$ts" "$((page_count + 1))"
      printf '[extra]\nmath = true\n+++\n\n'
      cat "$tmp"
    } > "$dest"
    rm -f "$tmp"
    page_count=$((page_count + 1))
  done < <(find "$RESULTS_DIR" -name '*.md' -print0 | sort -z)

  echo "Done. Synced ${page_count} page(s)."
}

sync_content

echo ""
echo "Starting zola serve on http://localhost:${PORT} ..."
echo ""
cd "$SITE_DIR"
exec zola serve --port "$PORT" --interface 127.0.0.1
