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

if [ "${1:-}" = "--aviso" ]; then
  python3 - "$HOME/.claude/settings.json" "$skills/artia/aviso.py" <<'PY'
import json, os, sys
caminho, script = sys.argv[1], sys.argv[2]
cfg = json.load(open(caminho)) if os.path.exists(caminho) else {}
comando = "python3 %s" % script
grupos = cfg.setdefault("hooks", {}).setdefault("SessionStart", [])
if any(h.get("command") == comando for g in grupos for h in g.get("hooks", [])):
    print("hook SessionStart do aviso já registrado")
else:
    grupos.append({"matcher": "", "hooks": [{"type": "command", "command": comando, "timeout": 6}]})
    with open(caminho, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("hook SessionStart do aviso registrado em %s" % caminho)
PY
fi
