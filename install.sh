#!/usr/bin/env bash
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
skills="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
backup="$HOME/.claude/skills-backup-$(date +%Y%m%d-%H%M%S)"

mkdir -p "$skills"
for name in artia artia-apontar artia-planejar; do
  target="$skills/$name"
  if [ -L "$target" ]; then
    rm "$target"
  elif [ -e "$target" ]; then
    mkdir -p "$backup"
    mv "$target" "$backup/$name"
    echo "backup: $backup/$name"
  fi
  ln -s "$repo/skills/$name" "$target"
  echo "link: $target -> $repo/skills/$name"
done
