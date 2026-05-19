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

    # ── config/secrets.json ──
    # 文件被 .gitignore 排除，worktree 中不会自动出现；主 repo 中可能是符号链接
    if [ ! -f "$wt/config/secrets.json" ]; then
        if [ -f "$MAIN_ROOT/config/secrets.json" ]; then
            cp "$(readlink -f "$MAIN_ROOT/config/secrets.json")" "$wt/config/secrets.json"
            echo "[hook] secrets.json created: $wt"
        fi
    elif [ -L "$wt/config/secrets.json" ]; then
        target=$(readlink -f "$wt/config/secrets.json")
        if [ -f "$target" ]; then
            cp "$target" "$wt/config/secrets.json"
            echo "[hook] secrets.json resolved (symlink → real file): $wt"
        fi
    fi

    # ── .claude/ 符号链接（skills/rules/agents/CLAUDE.md/settings.json）──
    if [ -d "$wt/docs/agent/skills" ] && [ ! -e "$wt/.claude/skills" ]; then
        mkdir -p "$wt/.claude"
        ln -sf "$wt/docs/agent/rules" "$wt/.claude/rules"
        ln -sf "$wt/docs/agent/skills" "$wt/.claude/skills"
        ln -sf "$wt/docs/agent/agents" "$wt/.claude/agents"
        ln -sf "$wt/docs/agent/rules/CLAUDE.md" "$wt/.claude/CLAUDE.md"
        ln -sf "$wt/docs/agent/settings.json" "$wt/.claude/settings.json"
        echo "[hook] .claude links created: $wt"
    fi
done
