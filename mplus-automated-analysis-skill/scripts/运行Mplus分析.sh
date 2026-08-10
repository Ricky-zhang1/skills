#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.mplusflow-venv"

find_python() {
  command -v python3.12 2>/dev/null || command -v python3 2>/dev/null || true
}

ensure_supported_python() {
  local candidate="$1"
  "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'
}

if [[ "${1:-}" == "bootstrap" ]]; then
  shift
  if [[ "${1:-}" != "--yes" ]]; then
    echo "环境尚未改动。请在用户明确同意后运行：./scripts/运行Mplus分析.sh bootstrap --yes" >&2
    exit 2
  fi

  PYTHON="$(find_python)"
  if [[ -z "$PYTHON" ]] || ! ensure_supported_python "$PYTHON"; then
    if command -v brew >/dev/null 2>&1; then
      echo "正在通过 Homebrew 安装 Python 3.12..."
      brew install python@3.12
      PYTHON="$(find_python)"
    else
      echo "未找到 Python 3.10+，且未找到 Homebrew。请先安装 Python 3.10+，再重新运行 bootstrap --yes。" >&2
      exit 2
    fi
  fi
  if [[ -z "$PYTHON" ]] || ! ensure_supported_python "$PYTHON"; then
    echo "Python 3.10+ 安装后仍不可用，请重新打开终端后再运行 bootstrap --yes。" >&2
    exit 2
  fi

  "$PYTHON" -m venv "$VENV"
  "$VENV/bin/python" -m pip install --upgrade pip
  "$VENV/bin/python" -m pip install -r "$ROOT/runtime/requirements.txt"
  export PYTHONPATH="$ROOT/runtime${PYTHONPATH:+:$PYTHONPATH}"
  exec "$VENV/bin/python" -m mplusflow doctor
fi

if [[ -x "$VENV/bin/python" ]]; then
  PYTHON="$VENV/bin/python"
else
  PYTHON="$(find_python)"
fi
if [[ -z "$PYTHON" ]]; then
  echo "未找到 Python。请让 Agent 征得你的同意后运行：./scripts/运行Mplus分析.sh bootstrap --yes" >&2
  exit 2
fi

export PYTHONPATH="$ROOT/runtime${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" -m mplusflow "$@"
