#!/usr/bin/env python3
import base64
import datetime as dt
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from diario import TZ, TITULOS_STATUS, dia_util_anterior

CONFIG_FILE = os.path.expanduser("~/.config/artia/config.json")
CACHE = os.path.expanduser("~/.cache/artia/ultimo.json")
INTERVALO_BUSCA_S = 30 * 60
HORA_LIMITE_ROTINA = dt.time(7, 0)


def ler_json(caminho):
    try:
        with open(caminho) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def buscar_remoto(repo, caminho):
    r = subprocess.run(["gh", "api", "repos/%s/contents/%s" % (repo, caminho)],
                       capture_output=True, text=True, timeout=4)
    if r.returncode != 0:
        return None
    return json.loads(base64.b64decode(json.loads(r.stdout)["content"]))


def ultimo_relatorio(cfg, esperado):
    destino = cfg.get("relatorios") or {}
    cache = ler_json(CACHE)
    recente = os.path.exists(CACHE) and time.time() - os.path.getmtime(CACHE) < INTERVALO_BUSCA_S
    if cache and (cache.get("data") == esperado.isoformat() or recente):
        return cache
    if not destino.get("repo"):
        return cache
    try:
        remoto = buscar_remoto(destino["repo"], destino.get("path", "relatorios") + "/ultimo.json")
    except (subprocess.SubprocessError, OSError, ValueError, KeyError):
        remoto = None
    if remoto is None:
        return cache
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w") as f:
        json.dump(remoto, f, ensure_ascii=False)
    return remoto


def resumo(r, esperado, agora):
    if not r or r.get("data", "") < esperado.isoformat():
        if agora.time() < HORA_LIMITE_ROTINA:
            return None
        ultimo = r.get("data") if r else "nunca"
        return "Artia: a rotina de apontamento não rodou para %s (último relatório: %s)." % (esperado, ultimo)
    linhas = ["Artia %s — %s" % (r["data"], TITULOS_STATUS.get(r["status"], r["status"]))]
    if r.get("erro"):
        linhas.append("  erro: " + r["erro"])
    for a in r.get("apontamentos") or []:
        linhas.append("  %s–%s %s (%s)" % (a["inicio"], a["fim"], a["atividade"][:60], a["obs"]))
    for p in r.get("pendencias") or []:
        linhas.append("  pendente: " + p)
    return "\n".join(linhas)


def main():
    cfg = ler_json(CONFIG_FILE) or {}
    agora = dt.datetime.now(TZ)
    esperado = dia_util_anterior(agora.date())
    texto = resumo(ultimo_relatorio(cfg, esperado), esperado, agora)
    if texto:
        print(json.dumps({"systemMessage": texto}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
