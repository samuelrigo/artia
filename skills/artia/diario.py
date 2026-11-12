#!/usr/bin/env python3
import argparse
import datetime as dt
import io
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import artia

TZ = ZoneInfo("America/Sao_Paulo")
PASSO_MIN = 30
BRANCHES_IGNORADAS = {"master", "main", "develop", "dev", "homolog", "staging", "release"}
DEFAULT_PERIODS = [{"start": "08:00", "end": "12:00"}, {"start": "13:30", "end": "18:00"}]

OBS_PR_ABERTA = "implementação e abertura de pr"
OBS_MUDANCAS_PR = "implementando mudanças solicitadas na pr"
OBS_REVIEW = "revisão de comentários da pr"
OBS_IMPLEMENTACAO = "planejamento e implementação da atividade"
OBS_ANALISE = "análise e planejamento da atividade"

PRIORIDADE_OBS = [
    ("pr_aberta", OBS_PR_ABERTA),
    ("push_apos_review", OBS_MUDANCAS_PR),
    ("review", OBS_REVIEW),
    ("push", OBS_IMPLEMENTACAO),
]


class FalhaDiario(Exception):
    pass


def dia_util_anterior(hoje):
    d = hoje - dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def para_minutos(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def para_hhmm(minutos):
    return "%02d:%02d" % divmod(minutos, 60)


def tarefas_da_branch(branch):
    resto = branch.split("/", 1)[1] if "/" in branch else branch
    m = re.match(r"(\d+(?:[-/]\d+)*)(?:[-/_]|$)", resto)
    if not m:
        return []
    return [int(n) for n in re.split(r"[-/]", m.group(1))]


def numero_da_tarefa(titulo):
    m = re.match(r"\s*tarefa\s+(\d+)\b", artia.normalize(titulo or ""))
    return int(m.group(1)) if m else None


def classificar(tipos):
    for tipo, obs in PRIORIDADE_OBS:
        if tipo in tipos:
            return obs
    return OBS_ANALISE


def repartir_passos(pesos, total_passos):
    if len(pesos) > total_passos:
        raise FalhaDiario("%d tarefas não cabem em blocos de %d min" % (len(pesos), PASSO_MIN))
    passos = [1] * len(pesos)
    sobra = total_passos - len(pesos)
    soma = sum(pesos)
    cotas = [p * sobra / soma for p in pesos]
    for i, c in enumerate(cotas):
        passos[i] += int(c)
    faltam = total_passos - sum(passos)
    ordem = sorted(range(len(pesos)), key=lambda i: (-(cotas[i] - int(cotas[i])), i))
    for i in ordem[:faltam]:
        passos[i] += 1
    return passos


def tarefas_por_janela(n, janelas):
    if n < len(janelas):
        return None
    return repartir_passos([fim - ini for ini, fim in janelas], n)


def dividir_horarios(tarefas, periods=None):
    periods = periods or DEFAULT_PERIODS
    janelas = [(para_minutos(p["start"]), para_minutos(p["end"])) for p in periods]
    if not tarefas:
        return []
    contagem = tarefas_por_janela(len(tarefas), janelas)
    if contagem is None:
        grupos = [(janela, tarefas) for janela in janelas]
    else:
        grupos, i = [], 0
        for janela, k in zip(janelas, contagem):
            grupos.append((janela, tarefas[i:i + k]))
            i += k
    blocos = []
    for (ini, fim), grupo in grupos:
        passos = repartir_passos([1] * len(grupo), (fim - ini) // PASSO_MIN)
        cursor = ini
        for tarefa, n in zip(grupo, passos):
            blocos.append({"tarefa": tarefa, "inicio": para_hhmm(cursor), "fim": para_hhmm(cursor + n * PASSO_MIN),
                           "horas": n * PASSO_MIN / 60})
            cursor += n * PASSO_MIN
    return blocos


def github_direto(caminho, token):
    req = urllib.request.Request("https://api.github.com/" + caminho, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "User-Agent": "artia-diario",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        raise FalhaDiario("GitHub %s: HTTP %d %s" % (caminho, e.code, e.read().decode(errors="replace")[:300]))
    except urllib.error.URLError as e:
        raise FalhaDiario("GitHub %s: %s" % (caminho, e))


def gh_api(caminho):
    token = os.environ.get("GH_TOKEN")
    if token:
        return github_direto(caminho, token)
    r = subprocess.run(["gh", "api", caminho], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise FalhaDiario("gh api %s: %s" % (caminho, (r.stderr or r.stdout).strip()[:300]))
    return json.loads(r.stdout or "null")


def eventos_do_repo(repo, dia):
    eventos = []
    for pagina in range(1, 4):
        lote = gh_api("repos/%s/events?per_page=100&page=%d" % (repo, pagina))
        if not lote:
            break
        eventos.extend(lote)
        mais_antigo = dt.datetime.fromisoformat(lote[-1]["created_at"].replace("Z", "+00:00")).astimezone(TZ)
        if mais_antigo.date() < dia:
            break
    return eventos


def eventos_do_github(usuario, dia, repos):
    alvo = usuario.lower()
    eventos, falhas = [], []
    for repo in repos:
        try:
            lote = eventos_do_repo(repo, dia)
        except FalhaDiario as e:
            falhas.append("%s: eventos do GitHub indisponíveis (%s)" % (repo, str(e)[:160]))
            continue
        eventos += [ev for ev in lote if (ev.get("actor") or {}).get("login", "").lower() == alvo]
    if falhas and len(falhas) == len(repos):
        raise FalhaDiario("; ".join(falhas))
    return eventos, falhas


def normalizar_evento_github(ev):
    tipo, payload = ev["type"], ev.get("payload") or {}
    pr = payload.get("pull_request") or {}
    if tipo == "PushEvent" and (payload.get("ref") or "").startswith("refs/heads/"):
        return "push", payload["ref"][len("refs/heads/"):], None
    if tipo == "CreateEvent" and payload.get("ref_type") == "branch":
        return "branch_criada", payload["ref"], None
    if tipo == "PullRequestEvent" and payload.get("action") == "opened":
        return "pr_aberta", pr.get("head", {}).get("ref"), pr.get("number")
    if tipo in ("PullRequestReviewEvent", "PullRequestReviewCommentEvent"):
        return "review", pr.get("head", {}).get("ref"), pr.get("number")
    return None


def movimentos_github(eventos, dia, repos_github):
    movimentos = []
    for ev in eventos:
        quando = dt.datetime.fromisoformat(ev["created_at"].replace("Z", "+00:00")).astimezone(TZ)
        repo = ev.get("repo", {}).get("name")
        if quando.date() != dia or repo not in repos_github:
            continue
        normalizado = normalizar_evento_github(ev)
        if not normalizado or not normalizado[1]:
            continue
        tipo, branch, pr = normalizado
        if branch in BRANCHES_IGNORADAS:
            continue
        movimentos.append({"quando": quando, "origem": "github", "repo": repo, "branch": branch,
                           "tipo": tipo, "pr": pr})
    return movimentos


def marcar_push_apos_review(movimentos, usuario):
    cache = {}
    for m in movimentos:
        if m["tipo"] != "push":
            continue
        chave = (m["repo"], m["branch"])
        if chave not in cache:
            dono = m["repo"].split("/")[0]
            prs = gh_api("repos/%s/pulls?state=all&head=%s:%s" % (m["repo"], dono, m["branch"])) or []
            reviews = []
            for pr in prs:
                reviews += [r for r in gh_api("repos/%s/pulls/%d/reviews" % (m["repo"], pr["number"])) or []
                            if (r.get("user") or {}).get("login", "").lower() != usuario.lower()]
            cache[chave] = [dt.datetime.fromisoformat(r["submitted_at"].replace("Z", "+00:00"))
                            for r in reviews if r.get("submitted_at")]
        if any(quando < m["quando"] for quando in cache[chave]):
            m["tipo"] = "push_apos_review"


def data_artia(texto):
    return dt.datetime.strptime(texto[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)


def listar_atividades(token, org, account, folder):
    q = ("query{ listingActivities(accountId: %d, folderId: %d) { id title updatedAt "
         "responsible { email } comments { createdAt author { email } } } }") % (account, folder)
    return artia.gql(q, token, org)["listingActivities"] or []


def movimentos_artia(atividades, dia, email, contexto):
    movimentos = []
    email = email.lower()
    for a in atividades:
        alvo = {"atividade": a, "contexto": contexto}
        for c in a.get("comments") or []:
            autor = ((c.get("author") or {}).get("email") or "").lower()
            if autor == email and c.get("createdAt") and data_artia(c["createdAt"]).date() == dia:
                movimentos.append(dict(alvo, quando=data_artia(c["createdAt"]), origem="artia", tipo="comentario_card"))
        responsavel = ((a.get("responsible") or {}).get("email") or "").lower()
        if responsavel == email and a.get("updatedAt") and data_artia(a["updatedAt"]).date() == dia:
            movimentos.append(dict(alvo, quando=data_artia(a["updatedAt"]), origem="artia", tipo="card_alterado"))
    return movimentos


def match_por_titulo(branch, atividades):
    alvo = set(artia.tokens(branch.split("/", 1)[-1]))
    placar = sorted(((len(alvo & set(artia.tokens(a["title"]))), a) for a in atividades),
                    key=lambda x: -x[0])
    if not placar or placar[0][0] < 2 or (len(placar) > 1 and placar[1][0] == placar[0][0]):
        return None
    return placar[0][1]


def resolver_atividades(mov, atividades):
    numeros = tarefas_da_branch(mov["branch"])
    if numeros:
        por_numero = {numero_da_tarefa(a["title"]): a for a in atividades}
        achadas = [por_numero[n] for n in numeros if n in por_numero]
        return achadas, [n for n in numeros if n not in por_numero]
    a = match_por_titulo(mov["branch"], atividades)
    return ([a], []) if a else ([], [None])


def agrupar(movimentos):
    tarefas = {}
    for m in sorted(movimentos, key=lambda m: m["quando"]):
        a = m["atividade"]
        t = tarefas.setdefault(a["id"], {"atividade": a, "contexto": m["contexto"], "tipos": [], "peso": 0,
                                         "primeiro": m["quando"]})
        t["tipos"].append(m["tipo"])
        t["peso"] += 1
    lista = sorted(tarefas.values(), key=lambda t: t["primeiro"])
    fortes = [t for t in lista if set(t["tipos"]) != {"card_alterado"}]
    lista = fortes or lista
    for t in lista:
        t["obs"] = classificar(set(t["tipos"]))
    return lista


def apontamentos_existentes(token, contextos, dia):
    existentes = []
    for org, account, folder in contextos:
        q = "query{ listingTimeEntries(accountId: %d, folderId: %d) { id dateAt startTime duration activityId } }" % (
            account, folder)
        existentes += [e for e in artia.gql(q, token, org)["listingTimeEntries"] or []
                       if (e.get("dateAt") or "")[:10] == dia.isoformat()]
    return existentes


def criar_apontamento(token, bloco, dia, cfg):
    ctx = bloco["tarefa"]["contexto"]
    padrao = cfg.get("defaults", {})
    campos = [
        "accountId: %d" % ctx["accountId"],
        "activityId: %d" % int(bloco["tarefa"]["atividade"]["id"]),
        'dateAt: "%s"' % dia.isoformat(),
        'startTime: "%s"' % bloco["inicio"],
        "duration: %s" % bloco["horas"],
        "timeEntryStatusId: %d" % int(padrao["timeEntryStatusId"]),
        'kindOfHours: "%s"' % artia.esc(padrao.get("kindOfHours", "normal")),
        'createdBy: "%s"' % artia.esc(cfg["createdBy"]),
        'observation: "%s"' % artia.esc(bloco["tarefa"]["obs"]),
    ]
    q = "mutation{ createTimeEntry(%s) { id } }" % ", ".join(campos)
    return artia.gql(q, token, ctx["organizationId"])["createTimeEntry"]["id"]


def contextos_do_config(cfg):
    repos_github = cfg.get("github", {}).get("repos", {})
    return repos_github, sorted({(c["organizationId"], c["accountId"], c["folderId"]) for c in repos_github.values()})


def executar(cfg, dia, gravar):
    repos_github, contextos = contextos_do_config(cfg)
    if not repos_github:
        raise FalhaDiario("config sem github.repos")
    usuario = cfg["github"]["user"]
    email = cfg["createdBy"]
    token = artia.get_token(argparse.Namespace())

    atividades_por_folder = {}
    movimentos = []
    for org, account, folder in contextos:
        contexto = {"organizationId": org, "accountId": account, "folderId": folder}
        atividades = listar_atividades(token, org, account, folder)
        atividades_por_folder[folder] = atividades
        movimentos += movimentos_artia(atividades, dia, email, contexto)

    eventos, pendencias = eventos_do_github(usuario, dia, repos_github)
    brutos = movimentos_github(eventos, dia, repos_github)
    marcar_push_apos_review(brutos, usuario)
    for m in brutos:
        c = repos_github[m["repo"]]
        contexto = {"organizationId": c["organizationId"], "accountId": c["accountId"], "folderId": c["folderId"]}
        achadas, faltando = resolver_atividades(m, atividades_por_folder[c["folderId"]])
        movimentos += [dict(m, atividade=a, contexto=contexto) for a in achadas]
        for n in faltando:
            motivo = "tarefa %s não encontrada no Artia" % n if n else "branch sem número de tarefa e sem match claro de título"
            pendencias.append("%s `%s` (%s): %s" % (m["repo"], m["branch"], m["tipo"], motivo))

    resultado = {"data": dia.isoformat(), "gravado": False, "apontamentos": [], "pendencias": sorted(set(pendencias))}
    tarefas = agrupar(movimentos)
    if not tarefas:
        resultado["status"] = "sem_movimentacao"
        return resultado

    existentes = apontamentos_existentes(token, contextos, dia)
    blocos = dividir_horarios(tarefas, cfg.get("defaults", {}).get("periods"))
    resultado["apontamentos"] = [
        {"atividade": b["tarefa"]["atividade"]["title"], "activityId": b["tarefa"]["atividade"]["id"],
         "inicio": b["inicio"], "fim": b["fim"], "horas": b["horas"], "obs": b["tarefa"]["obs"],
         "evidencias": sorted(set(b["tarefa"]["tipos"]))}
        for b in blocos
    ]
    if existentes:
        resultado["status"] = "ja_apontado"
        resultado["existentes"] = existentes
        return resultado
    if not gravar:
        resultado["status"] = "dry_run"
        return resultado
    for b, linha in zip(blocos, resultado["apontamentos"]):
        try:
            linha["id"] = criar_apontamento(token, b, dia, cfg)
        except SystemExit:
            criados = [l["id"] for l in resultado["apontamentos"] if "id" in l]
            raise FalhaDiario("falhou ao gravar %s %s; já gravados: %s" % (
                b["inicio"], linha["atividade"], ", ".join(map(str, criados)) or "nenhum"))
    resultado["status"] = "apontado"
    resultado["gravado"] = True
    return resultado


TITULOS_STATUS = {
    "apontado": "apontado no Artia",
    "dry_run": "simulação (nada gravado)",
    "sem_movimentacao": "sem movimentação — nada apontado",
    "ja_apontado": "já havia apontamento no dia — nada gravado",
    "erro": "falha — nada gravado",
}


def montar_relatorio(r):
    linhas = ["# Apontamento %s — %s" % (r["data"], TITULOS_STATUS.get(r["status"], r["status"])), ""]
    if r.get("erro"):
        linhas += ["Erro: `%s`" % r["erro"], ""]
    if r.get("apontamentos"):
        linhas += ["| início | fim | horas | atividade | observação | id |", "|---|---|---|---|---|---|"]
        for a in r["apontamentos"]:
            linhas.append("| %s | %s | %s | %s | %s | %s |" % (
                a["inicio"], a["fim"], a["horas"], a["atividade"], a["obs"], a.get("id", "—")))
        linhas.append("")
    if r.get("existentes"):
        linhas.append("Apontamentos já existentes no dia: " + ", ".join(
            "%s %sh (atividade %s)" % (e["startTime"], e["duration"], e["activityId"]) for e in r["existentes"]))
        linhas.append("")
    if r.get("pendencias"):
        linhas += ["Não apontado (resolver manualmente):"] + ["- " + p for p in r["pendencias"]] + [""]
    return "\n".join(linhas)


def salvar_relatorio(r, pasta):
    os.makedirs(pasta, exist_ok=True)
    with open(os.path.join(pasta, "%s.md" % r["data"]), "w") as f:
        f.write(montar_relatorio(r))
    with open(os.path.join(pasta, "ultimo.json"), "w") as f:
        json.dump(r, f, ensure_ascii=False, indent=2)


def main():
    p = argparse.ArgumentParser(description="Aponta no Artia o dia útil anterior a partir do GitHub e do Artia")
    p.add_argument("--date", help="YYYY-MM-DD; padrão: dia útil anterior a hoje")
    p.add_argument("--config", default=artia.CONFIG_FILE)
    p.add_argument("--report-dir", default=os.path.expanduser("~/.local/state/artia/relatorios"))
    p.add_argument("--yes", action="store_true", help="grava no Artia; sem isso é simulação")
    args = p.parse_args()

    dia = dt.date.fromisoformat(args.date) if args.date else dia_util_anterior(dt.datetime.now(TZ).date())
    stderr_original, sys.stderr = sys.stderr, io.StringIO()
    try:
        cfg = json.load(open(args.config))
        r = executar(cfg, dia, args.yes)
    except (FalhaDiario, SystemExit, OSError, ValueError, KeyError) as e:
        detalhe = sys.stderr.getvalue().strip().splitlines()
        mensagem = str(e) if not isinstance(e, SystemExit) or not isinstance(e.code, int) else ""
        r = {"data": dia.isoformat(), "status": "erro", "gravado": False,
             "erro": " | ".join(x for x in [mensagem, detalhe[-1] if detalhe else ""] if x)[:500] or type(e).__name__,
             "apontamentos": [], "pendencias": []}
    finally:
        avisos, sys.stderr = sys.stderr.getvalue(), stderr_original
        sys.stderr.write(avisos)
    r["executado_em"] = dt.datetime.now(TZ).isoformat(timespec="seconds")
    salvar_relatorio(r, args.report_dir)
    print(montar_relatorio(r))
    sys.exit(1 if r["status"] == "erro" else 0)


if __name__ == "__main__":
    main()
