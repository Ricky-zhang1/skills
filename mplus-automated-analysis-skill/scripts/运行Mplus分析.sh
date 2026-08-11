#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.mplusflow-venv"

find_python() {
  command -v python3.12 2>/dev/null || command -v python3 2>/dev/null || command -v python 2>/dev/null || true
}

ensure_supported_python() {
  local candidate="$1"
  "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'
}

runtime_ready() {
  local candidate="$1"
  [[ -x "$candidate" ]] || return 1
  ensure_supported_python "$candidate" || return 1
  "$candidate" -c 'import numpy, openpyxl, pandas, pyreadstat' >/dev/null 2>&1
}

find_ready_python() {
  local candidates=()
  local resolved=""
  [[ -x "$VENV/bin/python" ]] && candidates+=("$VENV/bin/python")
  for name in python3.12 python3 python; do
    resolved="$(command -v "$name" 2>/dev/null || true)"
    [[ -n "$resolved" ]] && candidates+=("$resolved")
  done
  for candidate in "${candidates[@]}"; do
    if runtime_ready "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
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

PYTHON="$(find_ready_python || true)"
if [[ -z "$PYTHON" ]]; then
  if [[ -n "$(find_python)" ]]; then
    echo "已找到 Python，但本 Skill 需要的数据读取依赖不齐。" >&2
  else
    echo "未找到 Python 3.10+。" >&2
  fi
  echo "环境尚未改动。请让 Agent 说明用途并征得你的同意后运行：./scripts/运行Mplus分析.sh bootstrap --yes" >&2
  exit 2
fi

export PYTHONPATH="$ROOT/runtime${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" -m mplusflow "$@"
