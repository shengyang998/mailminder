#!/bin/bash
# Mailminder installer: puts the `mailminder` command on PATH with uv, then runs the setup wizard.
set -euo pipefail
cd "$(dirname "$0")"

if [[ "$(uname)" != "Darwin" ]]; then
  echo "Mailminder 只支持 macOS。"
  exit 1
fi

if ! command -v claude >/dev/null 2>&1 && ! command -v codex >/dev/null 2>&1 \
   && [[ ! -x "$HOME/.local/bin/claude" ]]; then
  echo "需要先装 Claude Code 或 Codex 其中一个，并登录："
  echo "  Claude Code：https://claude.com/claude-code"
  echo "  Codex：npm install -g @openai/codex，然后运行 codex login"
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  if [[ -x "$HOME/.local/bin/uv" ]]; then
    export PATH="$HOME/.local/bin:$PATH"
  else
    echo "安装 uv（Python 工具管理器）……"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
  fi
fi

echo "安装 mailminder 命令……"
# --reinstall rebuilds from this checkout instead of reusing a cached build of an older one.
uv tool install --reinstall --quiet .
BIN="$(uv tool dir --bin)/mailminder"
echo "已安装：$BIN"
case ":$PATH:" in
  *":$(uv tool dir --bin):"*) ;;
  *) echo "提示：把 $(uv tool dir --bin) 加进 PATH（运行 uv tool update-shell），以后就能直接敲 mailminder。" ;;
esac
echo
exec "$BIN" init
