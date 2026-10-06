#!/usr/bin/env bash
# 一条本地命令启动「手术知情同意版本与撤回系统」：
#   ./run.sh            首次运行会自动安装依赖并写入演示数据
#   ./run.sh --reset    清空本地数据库与签署文件后重新写入演示数据
#
# 启动后：
#   前端（排期页）  http://127.0.0.1:5173
#   后端 API       http://127.0.0.1:8000   （文档 /docs）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
DATA_DIR="$BACKEND/data"
VENV_DIR="$ROOT/.venv"

# ---------- 0. 可选：重置演示数据 ----------
if [[ "${1:-}" == "--reset" ]]; then
  echo "[run] 清空本地数据目录 $DATA_DIR ..."
  rm -rf "$DATA_DIR"
fi

# ---------- 1. Python 环境与依赖 ----------
PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "[run] 未找到 python3，请先安装 Python 3.11+。"; exit 1
fi

# 优先使用项目内 venv；若无 venv 模块则回退到 --user 安装。
PIP_USE_USER=0
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  if "$PYTHON" -m venv "$VENV_DIR" >/dev/null 2>&1 && [[ -x "$VENV_DIR/bin/python" ]]; then
    echo "[run] 已创建虚拟环境 .venv"
  else
    echo "[run] venv 不可用，改用 --user 方式安装 Python 依赖"
    PIP_USE_USER=1
  fi
fi

if [[ "$PIP_USE_USER" == "0" ]]; then
  PY="$VENV_DIR/bin/python"
else
  PY="$PYTHON"
fi

if ! "$PY" -c "import fastapi, uvicorn, sqlalchemy" >/dev/null 2>&1; then
  if ! "$PY" -m pip --version >/dev/null 2>&1; then
    echo "[run] 未找到 pip，尝试通过 get-pip.py 引导安装 ..."
    TMP_PIP="$(mktemp -d)/get-pip.py"
    curl -sSL https://bootstrap.pypa.io/get-pip.py -o "$TMP_PIP"
    if [[ "$PIP_USE_USER" == "0" ]]; then
      "$PY" "$TMP_PIP"
    else
      "$PY" "$TMP_PIP" --user --break-system-packages
    fi
  fi
  echo "[run] 安装后端 Python 依赖 ..."
  if [[ "$PIP_USE_USER" == "0" ]]; then
    "$PY" -m pip install -q -r "$BACKEND/requirements.txt"
  else
    "$PY" -m pip install -q --user --break-system-packages -r "$BACKEND/requirements.txt"
  fi
fi

# ---------- 2. 前端依赖 ----------
if ! command -v npm >/dev/null 2>&1; then
  echo "[run] 未找到 npm，请先安装 Node.js 18+。"; exit 1
fi
if [[ ! -d "$FRONTEND/node_modules" ]]; then
  echo "[run] 安装前端依赖（npm install）..."
  (cd "$FRONTEND" && npm install --silent)
fi

# ---------- 3. 初始化数据库 & 演示数据 ----------
if [[ ! -f "$DATA_DIR/consent.db" ]]; then
  echo "[run] 初始化数据库并写入演示数据 ..."
  (cd "$BACKEND" && "$PY" -m seed)
else
  echo "[run] 使用既有数据库 $DATA_DIR/consent.db（如需重建：./run.sh --reset）"
fi

# ---------- 4. 同时启动后端与前端 ----------
echo "[run] 启动后端 http://127.0.0.1:8000 与前端 http://127.0.0.1:5173 ..."
( cd "$BACKEND" && "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 ) &
BACKEND_PID=$!
( cd "$FRONTEND" && npm run dev -- --host 127.0.0.1 --port 5173 ) &
FRONTEND_PID=$!

cleanup() {
  echo ""
  echo "[run] 正在停止服务 ..."
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
  wait "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[run] 已启动。浏览器打开 http://127.0.0.1:5173 （Ctrl+C 停止全部服务）"
wait
