#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "artia"))
import artia
from artia import html_to_text, normalize, tokens


def names_other_project(path, project_name, projects):
    base = tokens(os.path.basename(path))
    for other in projects:
        wanted = tokens(other["name"])
        if other["name"] != project_name and wanted and all(any(w in t for t in base) for w in wanted):
            return True
    return False


def local_dirs(folder_id, project_name, repos, projects):
    mapped = sorted(p for p, c in repos.items() if int(c["folderId"]) == int(folder_id))
    if mapped:
        roots = sorted({os.path.dirname(p) for p in mapped})
        found = set(mapped)
        for root in roots:
            for entry in os.listdir(root):
                path = os.path.join(root, entry)
                if os.path.isdir(os.path.join(path, ".git")) and not names_other_project(path, project_name, projects):
                    found.add(path)
        return {"origem": "config", "subrepos": sorted(found)}
    wanted = tokens(project_name)
    dev = os.path.expanduser("~/dev")
    guesses = []
    for root, dirs, _ in os.walk(dev):
        if ".git" in dirs and all(any(w in t for t in tokens(os.path.basename(root))) for w in wanted):
            guesses.append(root)
        if ".git" in dirs or root.count(os.sep) - dev.count(os.sep) >= 2:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules"]
    return {"origem": "palpite pelo nome" if guesses else "nao encontrado", "subrepos": sorted(guesses)}


def siblings(acts, title, own_id):
    m = re.match(r"^\s*tarefa\s*\d+\s*-\s*(.+?)\s*-\s*parte\s*\d+\s*$", normalize(title))
    if not m:
        return []
    stem = m.group(1)
    return [{"id": a["id"], "title": a["title"], "status": a["status"]}
            for a in acts if str(a["id"]) != str(own_id) and stem in normalize(a["title"])]


def main():
    p = argparse.ArgumentParser(description="Card do Artia por projeto do Artia + numero da tarefa")
    p.add_argument("args", nargs="+", metavar="[projeto-artia] tarefa")
    p.add_argument("--with-siblings", action="store_true")
    a = p.parse_args()
    project_arg, task = artia.split_task_args(a.args)
    ctx = artia.project_context(a, project_arg)
    cfg, project = ctx["cfg"], ctx["project"]
    hit, acts = artia.single_activity(ctx, task)

    act = artia.show(ctx["token"], cfg, hit["id"])
    out = {
        "projeto_artia": project["name"],
        "artia": cfg,
        "local": local_dirs(project["id"], project["name"], ctx["repos"], ctx["projects"]),
        "url": artia.activity_url(cfg, act["id"]),
        "id": act["id"],
        "title": act["title"],
        "concluida": act.get("status"),
        "situacao": (act.get("customStatus") or {}).get("statusName"),
        "responsavel": (act.get("responsible") or {}).get("name"),
        "previsto": [act.get("estimatedStart"), act.get("estimatedEnd")],
        "descricao": html_to_text(act.get("description")),
        "comentarios": [{"em": c.get("createdAt"), "texto": html_to_text(c.get("content"))}
                        for c in (act.get("comments") or [])],
    }
    if a.with_siblings or re.search(r"(?i)parte\s*\d+\s*$", act["title"]):
        out["partes_irmas"] = siblings(acts, act["title"], act["id"])
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
