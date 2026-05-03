#!/usr/bin/env bash
# Orchestrate the whole pipeline: clone source if missing, set up venv,
# convert HTML → out/, refine out/ → data/, validate data/, run tests.
#
# Usage: ./build.sh [--no-tests] [--no-validate]

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

SOURCE_REPO_URL="https://github.com/pedropasinn/Missale_romanum"
SOURCE_DIR="source/Missale_romanum"
VENV=".venv"
PY="$VENV/bin/python"

run_tests=1
run_validate=1
for arg in "$@"; do
  case "$arg" in
    --no-tests) run_tests=0 ;;
    --no-validate) run_validate=0 ;;
    *) echo "unknown arg: $arg"; exit 2 ;;
  esac
done

echo "==> Step 1: source clone"
if [[ ! -d "$SOURCE_DIR" ]]; then
  echo "  cloning $SOURCE_REPO_URL → $SOURCE_DIR"
  git clone --depth 1 "$SOURCE_REPO_URL" "$SOURCE_DIR"
else
  echo "  $SOURCE_DIR present; skipping clone"
fi

echo "==> Step 2: python env"
if [[ ! -x "$PY" ]]; then
  echo "  creating venv at $VENV"
  python3.11 -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet beautifulsoup4 lxml jsonschema pytest pytest-xdist
else
  echo "  $VENV present; skipping setup"
fi

echo "==> Step 3: convert (HTML → out/)"
"$PY" scripts/convert.py

echo "==> Step 4: refine (out/ → data/)"
"$PY" scripts/refine.py

if [[ "$run_validate" == "1" ]]; then
  echo "==> Step 5: validate"
  "$PY" scripts/validate.py
fi

if [[ "$run_tests" == "1" ]]; then
  echo "==> Step 6: tests"
  "$PY" -m pytest scripts/ -q -n auto
fi

echo "==> Done."
