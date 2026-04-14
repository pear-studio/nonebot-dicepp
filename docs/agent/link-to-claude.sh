#!/bin/bash
# 创建符号链接：docs/agent/ -> .claude/
# 将项目内的 agent 配置链接到 Claude Code 的 .claude/ 目录

set -e

# 获取脚本所在目录的绝对路径
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$REPO_ROOT"

# 检查源目录是否存在
if [ ! -d "docs/agent/rules" ]; then
    echo "错误: 源目录 docs/agent/rules 不存在"
    exit 1
fi

if [ ! -d "docs/agent/skills" ]; then
    echo "错误: 源目录 docs/agent/skills 不存在"
    exit 1
fi

if [ ! -d "docs/agent/agents" ]; then
    echo "错误: 源目录 docs/agent/agents 不存在"
    exit 1
fi

echo "正在创建符号链接..."

# 确保 .claude 目录存在
mkdir -p .claude

# 删除旧目录/链接
if [ -L ".claude/rules" ] || [ -d ".claude/rules" ]; then
    rm -rf .claude/rules
    echo "已删除 .claude/rules"
fi

if [ -L ".claude/skills" ] || [ -d ".claude/skills" ]; then
    rm -rf .claude/skills
    echo "已删除 .claude/skills"
fi

# 删除旧链接
if [ -L ".claude/agents" ] || [ -d ".claude/agents" ]; then
    rm -rf .claude/agents
    echo "已删除 .claude/agents"
fi

if [ -L ".claude/CLAUDE.md" ] || [ -f ".claude/CLAUDE.md" ]; then
    rm -f .claude/CLAUDE.md
    echo "已删除 .claude/CLAUDE.md"
fi

# 创建符号链接（使用相对路径）
ln -s "$REPO_ROOT/docs/agent/rules" .claude/rules
ln -s "$REPO_ROOT/docs/agent/skills" .claude/skills
ln -s "$REPO_ROOT/docs/agent/agents" .claude/agents
ln -s "$REPO_ROOT/docs/agent/rules/CLAUDE.md" .claude/CLAUDE.md

echo ""
echo "符号链接创建完成:"
echo "  .claude/rules      -> docs/agent/rules"
echo "  .claude/skills     -> docs/agent/skills"
echo "  .claude/agents     -> docs/agent/agents"
echo "  .claude/CLAUDE.md  -> docs/agent/rules/CLAUDE.md"
echo ""
echo "Skills:"
for dir in docs/agent/skills/*/; do
    if [ -d "$dir" ]; then
        echo "  - $(basename "$dir")"
    fi
done
