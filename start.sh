#!/usr/bin/env bash
# 一条本地命令启动：./start.sh
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "[start] 创建虚拟环境 .venv ..."
  if ! python3 -m venv .venv 2>/dev/null; then
    python3 -m venv .venv --without-pip
    curl -sSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
    .venv/bin/python /tmp/get-pip.py -q
  fi
fi

echo "[start] 安装依赖 ..."
.venv/bin/pip install -q -r requirements.txt

echo "[start] 启动服务： http://127.0.0.1:8000"
exec .venv/bin/python run.py
