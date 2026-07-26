#!/bin/bash
# EnterWorktree hook — 初始化 worktree 开发环境
# 从任意 worktree 或主 tree 均可运行，路径通过 git worktree list 动态定位
set -e

MAIN_ROOT=$(git worktree list --porcelain | grep '^worktree ' | head -1 | cut -d' ' -f2-)

for wt in "$MAIN_ROOT/.claude/worktrees/"*/; do
    [ -d "$wt" ] || continue

    # ── .venv 独立环境 ──
    # 如果 .venv 是符号链接（旧行为）或不存在，创建独立 venv
    if [ -L "$wt/.venv" ] || [ ! -d "$wt/.venv" ]; then
        rm -rf "$wt/.venv"
        uv venv "$wt/.venv"
        (cd "$wt" && uv sync)
        echo "[hook] .venv created (independent): $wt"
    fi

    # ── content/ 目录拷贝（被 .gitignore 排除但开发需要）──
    if [ -d "$MAIN_ROOT/content" ] && [ ! -d "$wt/content" ]; then
        cp -r "$MAIN_ROOT/content" "$wt/"
        echo "[hook] content/ copied: $wt"
    fi

    # ── .claude/ agent 配置同步 ──
    if [ -f "$wt/docs/agent/sync.py" ]; then
        if [ -f "$MAIN_ROOT/docs/agent/.agent-env.json" ] && [ ! -f "$wt/docs/agent/.agent-env.json" ]; then
            cp "$MAIN_ROOT/docs/agent/.agent-env.json" "$wt/docs/agent/.agent-env.json"
            echo "[hook] agent env copied: $wt"
        fi

        if command -v python3 >/dev/null 2>&1; then
            PYTHON_BIN=python3
        elif command -v python >/dev/null 2>&1; then
            PYTHON_BIN=python
        else
            echo "[hook] python not found; skipped agent sync: $wt"
            continue
        fi

        (cd "$wt" && "$PYTHON_BIN" docs/agent/sync.py apply claude --env dev)
        echo "[hook] .claude synced: $wt"
    fi
done
