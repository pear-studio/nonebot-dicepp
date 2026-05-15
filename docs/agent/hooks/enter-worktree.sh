#!/bin/bash
# EnterWorktree hook — 在新 worktree 中自动创建符号链接
# 从任意 worktree 或主 tree 均可运行，路径通过 git worktree list 动态定位
set -e

MAIN_ROOT=$(git worktree list --porcelain | grep '^worktree ' | head -1 | cut -d' ' -f2-)

for wt in "$MAIN_ROOT/.claude/worktrees/"*/; do
    [ -d "$wt" ] || continue

    # .venv 符号链接
    if [ ! -e "$wt/.venv" ]; then
        ln -sf "$MAIN_ROOT/.venv" "$wt/.venv"
        echo "[hook] .venv symlink created: $wt"
    fi

    # .claude/ 符号链接（skills/rules/agents/CLAUDE.md/settings.json）
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
