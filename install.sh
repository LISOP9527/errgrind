#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

python_signature() {
    "$1" -c 'import platform, sys; print(f"{sys.platform}|{sys.version_info.major}.{sys.version_info.minor}|{platform.machine().lower()}")'
}

ensure_venv_available() {
    if ! python3 -c 'import ensurepip' >/dev/null 2>&1; then
        echo "错误：当前 Python 缺少 venv/ensurepip 支持。"
        echo "Debian/Ubuntu 请先运行：apt-get install python3-venv"
        return 1
    fi
}

if [ -d ".venv" ]; then
    system_signature=$(python_signature python3)
    venv_signature=$(python_signature .venv/bin/python 2>/dev/null || true)
    if [ "$venv_signature" != "$system_signature" ] || \
        ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
        ensure_venv_available
        backup_path=".venv.backup-$(date -u +%Y%m%dT%H%M%SZ)-$$"
        echo "检测到旧虚拟环境不完整或与当前 Python 不兼容："
        echo "  旧环境: ${venv_signature:-无法启动}"
        echo "  当前环境: $system_signature"
        mv .venv "$backup_path"
        echo "旧环境已备份到 $backup_path"
    fi
fi

if [ ! -d ".venv" ]; then
    ensure_venv_available
    echo "创建虚拟环境..."
    python3 -m venv .venv
fi

echo "安装依赖..."
if ! .venv/bin/pip install -e '.[codex]' --quiet; then
    echo "⚠ Codex 可选依赖安装失败，将继续安装 Gemini / DeepSeek / OpenCode 支持。"
    .venv/bin/pip install -e . --quiet
fi

USER_BIN="$HOME/.local/bin"
if [[ ":$PATH:" == *":$USER_BIN:"* ]]; then
    TARGET="$USER_BIN/errgrind"
elif [[ ":$PATH:" == *":/usr/local/bin:"* ]] && \
    [ -d "/usr/local/bin" ] && [ -w "/usr/local/bin" ]; then
    # root/PRoot 环境通常不会把 ~/.local/bin 加入 PATH，
    # 因此放到已在 PATH 内的全局命令目录。
    TARGET="/usr/local/bin/errgrind"
else
    TARGET="$USER_BIN/errgrind"
fi

mkdir -p "$(dirname "$TARGET")"
ln -sf "$PWD/.venv/bin/errgrind" "$TARGET"

if command -v errgrind >/dev/null 2>&1; then
    echo "✓ 安装完成，运行 errgrind 启动"
else
    echo "✓ 安装完成，命令已安装到 $TARGET"
    echo "当前 PATH 中没有 $USER_BIN，请先运行："
    echo "  export PATH=\"$USER_BIN:\$PATH\""
    echo "然后运行 errgrind 启动。"
fi
