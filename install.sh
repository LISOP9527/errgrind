#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "创建虚拟环境..."
    python3 -m venv .venv
fi

echo "安装依赖..."
.venv/bin/pip install -e . --quiet

TARGET="$HOME/.local/bin/errgrind"
mkdir -p "$HOME/.local/bin"
ln -sf "$PWD/.venv/bin/errgrind" "$TARGET"

echo "✓ 安装完成，运行 errgrind 启动"
