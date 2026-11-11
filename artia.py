#!/usr/bin/env python3
"""
Cliente mínimo (só stdlib) pra API GraphQL do Artia (https://api.artia.com/graphql).
Usado pela skill /artia-apontar. Credenciais: ARTIA_EMAIL/ARTIA_PASSWORD no
ambiente (não persiste entre chamadas de shell não-interativo, ex: Bash tool do
Claude Code) ou ~/.config/artia/credentials.json (0600, gravado via
`set-credentials`) — env tem prioridade sobre o arquivo. Só o token JWT é
cacheado (curta duração, arquivo 0600) pra não logar de novo a cada chamada
dentro da mesma sessão.

Subcomandos:
  set-credentials [--email E]             grava ~/.config/artia/credentials.json (0600); pede senha via getpass
  login                                   autentica, cacheia token
  whoami                                  confirma token, mostra usuário(s) da organização
  projects  --account ID                  lista projetos do grupo de trabalho
  folders   --account ID [--page N]       lista pastas/projetos (paginado, 25/página)
  activities --folder ID [--account ID] [--mine] [--email E]
                                           lista atividades de uma pasta
  time-entries --account ID --activity ID [--folder ID] [--date YYYY-MM-DD]
                                           lista apontamentos (filtra data no cliente)
  create-entry --account ID --activity ID --date D --start HH:MM --duration H
               [--status ID] [--by EMAIL] [--kind normal|extra] [--obs TEXT]
               [--custom-field HASH=VALOR ...] [--yes]
                                           cria apontamento — sem --yes só mostra o payload (dry-run)
  raw --query 'graphql...'                escape hatch pra qualquer query/mutation da coleção

Todas as respostas saem em JSON no stdout (fácil de parsear depois).
"""
import argparse
import base64
import getpass
import html
import json
import os
import re
import stat
import sys
import time
import unicodedata
import urllib.error
import urllib.request

API_URL = "https://api.artia.com/graphql"
TOKEN_CACHE = os.path.expanduser("~/.cache/artia/token.json")
CREDENTIALS_FILE = os.path.expanduser("~/.config/artia/credentials.json")
CONFIG_FILE = os.path.expanduser("~/.config/artia/config.json")
APP_URL = "https://app.artia.com"
STOPWORDS = {"de", "do", "da", "no", "na", "em", "desenvolvimento", "manutencao", "portal"}


def load_repos():
    try:
        return json.load(open(CONFIG_FILE)).get("repos", {})
    except (OSError, ValueError):
        return {}


def config_org():
    orgs = {c.get("organizationId") for c in load_repos().values()} - {None}
    return str(orgs.pop()) if len(orgs) == 1 else "1"


DEFAULT_ORG = os.environ.get("ARTIA_ORG_ID") or config_org()


def esc(s):
    """Escapa string pra literal GraphQL inline (a coleção oficial usa valores
    inline, sem variables, então seguimos o mesmo padrão)."""
    return (
        str(s)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )


def gql(query, token=None, org_id=None):
    headers = {
        "Content-Type": "application/json",
        # Cloudflare bloqueia (403 / error 1010) o User-Agent default do
        # urllib ("Python-urllib/x.y"); um UA de browser passa.
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["OrganizationId"] = str(org_id or DEFAULT_ORG)
    data = json.dumps({"query": query}).encode("utf-8")
    req = urllib.request.Request(API_URL, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"HTTP {e.code}: {e.read().decode(errors='replace')}\n")
        sys.exit(1)
    except urllib.error.URLError as e:
        sys.stderr.write(f"Falha de rede: {e}\n")
        sys.exit(1)
    if body.get("errors"):
        sys.stderr.write("Erro GraphQL: " + json.dumps(body["errors"], ensure_ascii=False) + "\n")
        sys.exit(1)
    return body["data"]


def jwt_exp(token):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("exp")
    except Exception:
        return None


def load_cached_token():
    if not os.path.exists(TOKEN_CACHE):
        return None
    try:
        d = json.load(open(TOKEN_CACHE))
        if d.get("exp", 0) > time.time() + 60:
            return d["token"]
    except Exception:
        pass
    return None


def save_token(token):
    os.makedirs(os.path.dirname(TOKEN_CACHE), exist_ok=True)
    with open(TOKEN_CACHE, "w") as f:
        json.dump({"token": token, "exp": jwt_exp(token) or (time.time() + 3000)}, f)
    os.chmod(TOKEN_CACHE, 0o600)


def load_credentials():
    """Lê ~/.config/artia/credentials.json (email/password). Retorna (None, None)
    se não existir. Avisa (não aborta) se a permissão for mais frouxa que 0600 —
    esse arquivo guarda senha em texto plano, permissão errada expõe pra outros
    usuários da máquina."""
    if not os.path.exists(CREDENTIALS_FILE):
        return None, None
    try:
        mode = stat.S_IMODE(os.stat(CREDENTIALS_FILE).st_mode)
        if mode & 0o077:
            sys.stderr.write(
                f"Aviso: {CREDENTIALS_FILE} tem permissão {oct(mode)} (deveria ser "
                f"0600 — só o dono lê). Rode: chmod 600 {CREDENTIALS_FILE}\n"
            )
        d = json.load(open(CREDENTIALS_FILE))
        return d.get("email"), d.get("password")
    except Exception:
        return None, None


def get_current_email():
    """Email do usuário logado: env tem prioridade, senão o do arquivo de
    credenciais. Usado por --mine e afins, que precisam do email mesmo quando
    a senha nunca passou por env var."""
    return os.environ.get("ARTIA_EMAIL") or load_credentials()[0]


def do_login(email=None, password=None):
    cred_email, cred_password = (None, None)
    if not (email and password):
        cred_email, cred_password = load_credentials()
    email = email or os.environ.get("ARTIA_EMAIL") or cred_email
    password = password or os.environ.get("ARTIA_PASSWORD") or cred_password
    if not email or not password:
        sys.exit(
            "Faltam credenciais. Grave uma vez com:\n"
            "  python3 artia.py set-credentials --email seu@email\n"
            f"(grava {CREDENTIALS_FILE}, permissão 0600 — persiste entre chamadas)\n"
            "Ou, como alternativa, exporte ARTIA_EMAIL/ARTIA_PASSWORD no ambiente "
            "(não persiste em shells não-interativos, ex: Bash tool do Claude Code)."
        )
    q = f'mutation{{ authenticationByEmail(email:"{esc(email)}", password:"{esc(password)}") {{ token }} }}'
    data = gql(q)
    token = data["authenticationByEmail"]["token"]
    save_token(token)
    return token


def get_token(args):
    token = load_cached_token()
    if token:
        return token
    token = do_login(getattr(args, "email", None), getattr(args, "password", None))
    return token


def fail(payload):
    sys.exit(json.dumps(payload, ensure_ascii=False))


def normalize(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return s.lower()


def html_to_text(raw):
    if not raw:
        return ""
    s = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    s = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "- ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n\s*\n+", "\n\n", s)
    return s.strip()


def tokens(text):
    return [t for t in re.split(r"[^a-z0-9]+", normalize(text)) if t and t not in STOPWORDS]


def is_task(arg):
    return re.fullmatch(r"(?i)(t|tarefa)?\s*\d+", arg.strip()) is not None


def org_and_account(repos):
    pairs = {(c.get("organizationId"), c.get("accountId")) for c in repos.values()}
    if len(pairs) != 1:
        fail({"error": "config com mais de uma organizacao/grupo; defina no config"})
    return pairs.pop()


def folder_from_cwd(repos):
    cwd = os.path.realpath(os.getcwd())
    folders = {c["folderId"] for path, c in repos.items()
               if cwd == path or cwd.startswith(path + os.sep) or path.startswith(cwd + os.sep)}
    return folders.pop() if len(folders) == 1 else None


def resolve_artia_project(name, projects, repos):
    if name is None:
        folder = folder_from_cwd(repos)
        hits = [p for p in projects if int(p["id"]) == folder]
    elif name.isdigit():
        hits = [p for p in projects if str(p["id"]) == name]
    else:
        wanted = tokens(name) or [normalize(name)]
        hits = [p for p in projects if all(any(w in t for t in tokens(p["name"])) for w in wanted)]
    if len(hits) == 1:
        return hits[0]
    fail({"error": "informe o projeto do Artia" if name is None else "projeto do Artia ambiguo ou desconhecido",
          "arg": name, "projetos": [p["name"] for p in (hits or projects)]})


def split_task_args(words):
    task_args = [x for x in words if is_task(x)]
    name_args = [x for x in words if not is_task(x)]
    if len(task_args) == 2 and not name_args:
        name_args, task_args = task_args[:1], task_args[1:]
    if len(task_args) != 1:
        fail({"error": "informe um numero de tarefa", "args": words})
    return " ".join(name_args) or None, re.sub(r"(?i)^(t|tarefa)\s*", "", task_args[0])


def account_context(args):
    repos = load_repos()
    org, account = org_and_account(repos)
    token = get_token(args)
    projects = gql("query{ listingProjects(accountId: %d) { id name } }" % account, token, org)["listingProjects"]
    return {"token": token, "org": org, "account": account, "repos": repos, "projects": projects}


def project_context(args, project_arg):
    ctx = account_context(args)
    project = resolve_artia_project(project_arg, ctx["projects"], ctx["repos"])
    ctx["project"] = project
    ctx["cfg"] = {"organizationId": ctx["org"], "accountId": ctx["account"], "folderId": int(project["id"])}
    return ctx


def list_activities(token, cfg):
    data = gql(
        "query{ listingActivities(accountId: %d, folderId: %d) "
        "{ id title status customStatus { id statusName } responsible { id name email } } }"
        % (cfg["accountId"], cfg["folderId"]), token, cfg["organizationId"])
    return data.get("listingActivities") or []


def find_activity(token, cfg, number):
    acts = list_activities(token, cfg)
    pattern = re.compile(r"^\s*tarefa\s*0*%s\b" % re.escape(str(int(number))), re.I)
    hits = [a for a in acts if pattern.search(normalize(a["title"]))]
    if not hits:
        hits = [a for a in acts if str(a["id"]) == str(number)]
    return hits, acts


def single_activity(ctx, number):
    hits, acts = find_activity(ctx["token"], ctx["cfg"], number)
    if len(hits) != 1:
        fail({"error": "nenhuma atividade" if not hits else "mais de uma atividade",
              "projeto": ctx["project"]["name"],
              "candidatas": [{"id": h["id"], "title": h["title"]} for h in hits]})
    return hits[0], acts


def show(token, cfg, activity_id):
    q = ("query{ showActivity(id: %s, accountId: %d, folderId: %d) "
         "{ id title description status estimatedStart estimatedEnd completedPercent "
         "responsible { name email } customStatus { statusName } "
         "comments { content createdAt } } }") % (activity_id, cfg["accountId"], cfg["folderId"])
    return gql(q, token, cfg["organizationId"])["showActivity"]


def activity_url(cfg, activity_id):
    return "%s/a/%d/f/%d/activities/%s" % (APP_URL, cfg["accountId"], cfg["folderId"], activity_id)


def activity_line(a, prefix=None):
    cols = [str(a["id"]), (a.get("customStatus") or {}).get("statusName") or "-",
            (a.get("responsible") or {}).get("name") or "-", a["title"]]
    return " | ".join(([prefix] if prefix else []) + cols)


def activity_statuses(ctx):
    data = gql("query{ listingCustomStatus(accounts: [%d]) { id statusName statusObject status inactive } }"
               % ctx["account"], ctx["token"], ctx["org"])
    return [s for s in data["listingCustomStatus"] if s["statusObject"] == "Activity" and not s.get("inactive")]


def resolve_status(ctx, wanted):
    statuses = activity_statuses(ctx)
    if wanted.isdigit():
        hits = [s for s in statuses if str(s["id"]) == wanted]
    else:
        hits = [s for s in statuses if normalize(s["statusName"]) == normalize(wanted)]
        hits = hits or [s for s in statuses if normalize(wanted) in normalize(s["statusName"])]
    if len(hits) != 1:
        fail({"error": "situacao ambigua ou desconhecida", "arg": wanted,
              "situacoes": ["%s | %s" % (s["id"], s["statusName"]) for s in (hits or statuses)]})
    return hits[0]


def resolve_user(acts, wanted):
    seen = {r["id"]: r for r in (a.get("responsible") for a in acts) if r and r.get("id")}
    hits = [u for u in seen.values()
            if normalize(wanted) in normalize(u.get("email") or "") or normalize(wanted) in normalize(u.get("name") or "")]
    if len(hits) != 1:
        fail({"error": "responsavel ambiguo ou sem tarefa neste projeto", "arg": wanted,
              "candidatos": ["%s | %s" % (u["name"], u["email"]) for u in (hits or seen.values())]})
    return hits[0]


def run_mutation(args, ctx, query, field, summary):
    if not args.yes:
        print("DRY-RUN (nada gravado) — " + summary)
        print(query)
        print("Para gravar: mesmo comando com --yes, depois de confirmação explícita do usuário.")
        return
    data = gql(query, ctx["token"], ctx["org"])
    print("OK — " + summary + " | retorno: " + json.dumps(data[field], ensure_ascii=False))


# ---- subcomandos ----

def cmd_set_credentials(args):
    """Grava ~/.config/artia/credentials.json (0600). Senha nunca é aceita como
    argumento de linha de comando (vazaria pro histórico do shell e pra 'ps') —
    só via prompt getpass (sem echo) ou, como fallback não-interativo, ARTIA_PASSWORD
    já setada na sessão atual."""
    email = args.email or input("Email Artia: ").strip()
    if not email:
        sys.exit("Email vazio, abortando.")
    password = os.environ.get("ARTIA_PASSWORD")
    if not password:
        if sys.stdin.isatty():
            password = getpass.getpass("Senha Artia (não aparece na tela): ")
        else:
            password = sys.stdin.readline().rstrip("\n")
    if not password:
        sys.exit("Senha vazia, abortando.")
    os.makedirs(os.path.dirname(CREDENTIALS_FILE), exist_ok=True)
    fd = os.open(CREDENTIALS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"email": email, "password": password}, f)
    os.chmod(CREDENTIALS_FILE, 0o600)
    print(json.dumps({"status": "OK", "file": CREDENTIALS_FILE, "perm": "600"}, ensure_ascii=False))


def cmd_login(args):
    token = do_login(args.email, args.password)
    exp = jwt_exp(token)
    print(json.dumps({"status": "OK", "cached_at": TOKEN_CACHE, "exp": exp}, ensure_ascii=False))


def cmd_whoami(args):
    token = get_token(args)
    q = "query{ listingOrganizationUsers{ id, userId, name, email, role, suspended } }"
    data = gql(q, token, args.org)
    users = data["listingOrganizationUsers"]
    if args.email:
        users = [u for u in users if u.get("email", "").lower() == args.email.lower()]
    print(json.dumps(users, ensure_ascii=False, indent=2))


def cmd_projects(args):
    token = get_token(args)
    q = f"query{{ listingProjects(accountId: {int(args.account)}) {{ id, accountId, status, name, costCenterId }} }}"
    data = gql(q, token, args.org)
    print(json.dumps(data["listingProjects"], ensure_ascii=False, indent=2))


def cmd_folders(args):
    token = get_token(args)
    page = f", page: {int(args.page)}" if args.page else ""
    q = f"""query{{ listingFolders(accountId: {int(args.account)}{page}) {{
        id, name, status, accountId, folderTypeName, estimatedStart, estimatedEnd
    }} }}"""
    data = gql(q, token, args.org)
    print(json.dumps(data["listingFolders"], ensure_ascii=False, indent=2))


def cmd_activities(args):
    token = get_token(args)
    acc = f"accountId: {int(args.account)}, " if args.account else ""
    q = f"""query{{ listingActivities({acc}folderId: {int(args.folder)}) {{
        id, uid, title, status, folderTypeName,
        estimatedStart, estimatedEnd, actualStart, actualEnd,
        responsible {{ id, name, email }}
    }} }}"""
    data = gql(q, token, args.org)
    acts = data["listingActivities"]
    if args.mine:
        email = (args.email or get_current_email() or "").lower()
        acts = [a for a in acts if (a.get("responsible") or {}).get("email", "").lower() == email]
    print(json.dumps(acts, ensure_ascii=False, indent=2))


def cmd_time_entries(args):
    token = get_token(args)
    folder = f"folderId: {int(args.folder)}, " if args.folder else ""
    q = f"""query{{ listingTimeEntries(accountId: {int(args.account)}, {folder}activityId: {int(args.activity)}) {{
        id, accountId, folderId, activityId, dateAt, startTime, endTime, duration,
        observation, timeEntryStatusId, status, kindOfHours
    }} }}"""
    data = gql(q, token, args.org)
    entries = data["listingTimeEntries"]
    if args.date:
        entries = [e for e in entries if (e.get("dateAt") or "")[:10] == args.date]
    print(json.dumps(entries, ensure_ascii=False, indent=2))


def cmd_create_entry(args):
    fields = [
        f"accountId: {int(args.account)}",
        f"activityId: {int(args.activity)}",
        f'dateAt: "{esc(args.date)}"',
        f'startTime: "{esc(args.start)}"',
        f"duration: {float(args.duration)}",
        f"timeEntryStatusId: {int(args.status)}",
        f'kindOfHours: "{esc(args.kind)}"',
    ]
    if args.by:
        fields.append(f'createdBy: "{esc(args.by)}"')
    if args.obs:
        fields.append(f'observation: "{esc(args.obs)}"')
    if args.custom_field:
        cfs = []
        for cf in args.custom_field:
            hash_field, _, value = cf.partition("=")
            cfs.append(f'{{ hashField: "{esc(hash_field)}", value: "{esc(value)}" }}')
        fields.append("customField: [" + ", ".join(cfs) + "]")

    q = f"""mutation{{ createTimeEntry({", ".join(fields)}) {{
        id, accountId, folderId, activityId, dateAt, startTime, endTime, duration,
        observation, timeEntryStatusId, kindOfHours
    }} }}"""

    if not args.yes:
        print(json.dumps({"dry_run": True, "would_send_query": q}, ensure_ascii=False, indent=2))
        print("\n-- nada foi criado. Rode de novo com --yes para confirmar. --", file=sys.stderr)
        return

    token = get_token(args)
    data = gql(q, token, args.org)
    print(json.dumps(data["createTimeEntry"], ensure_ascii=False, indent=2))


def cmd_raw(args):
    token = None if args.noauth else get_token(args)
    data = gql(args.query, token, args.org)
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_projetos(args):
    ctx = account_context(args)
    mapped = {int(c["folderId"]) for c in ctx["repos"].values()}
    for p in ctx["projects"]:
        print("%s | %s%s" % (p["id"], p["name"], " | no config" if int(p["id"]) in mapped else ""))


def cmd_card(args):
    project_arg, number = split_task_args(args.args)
    ctx = project_context(args, project_arg)
    hit, _ = single_activity(ctx, number)
    act = show(ctx["token"], ctx["cfg"], hit["id"])
    comments = act.get("comments") or []
    print(act["title"])
    print(" | ".join([
        "projeto: " + ctx["project"]["name"],
        "situação: " + ((act.get("customStatus") or {}).get("statusName") or "-"),
        "concluída: " + ("sim" if act.get("status") else "não"),
        "responsável: " + ((act.get("responsible") or {}).get("name") or "-"),
        "previsto: %s → %s" % ((act.get("estimatedStart") or "-")[:10], (act.get("estimatedEnd") or "-")[:10]),
    ]))
    print(activity_url(ctx["cfg"], act["id"]))
    print("\nDESCRIÇÃO\n" + (html_to_text(act.get("description")) or "(vazia)"))
    print("\nCOMENTÁRIOS (%d)" % len(comments))
    for c in comments:
        print("[%s] %s" % ((c.get("createdAt") or "")[:16], html_to_text(c.get("content"))))


def listing_targets(args, project_arg):
    ctx = account_context(args)
    if project_arg is None and folder_from_cwd(ctx["repos"]) is None:
        folders = {int(c["folderId"]) for c in ctx["repos"].values()}
        projects = [p for p in ctx["projects"] if int(p["id"]) in folders]
    else:
        projects = [resolve_artia_project(project_arg, ctx["projects"], ctx["repos"])]
    return ctx, projects


def print_activities(args, keep):
    ctx, projects = listing_targets(args, " ".join(args.projeto) or None)
    total = 0
    for project in projects:
        cfg = {"organizationId": ctx["org"], "accountId": ctx["account"], "folderId": int(project["id"])}
        acts = [a for a in list_activities(ctx["token"], cfg) if keep(a)]
        if args.abertas:
            acts = [a for a in acts if not a.get("status")]
        if acts and len(projects) > 1:
            print("## %s (%s)" % (project["name"], project["id"]))
        for a in acts:
            print(activity_line(a))
        total += len(acts)
    if not total:
        print("nenhuma tarefa")


def cmd_minhas(args):
    email = (args.email or get_current_email() or "").lower()
    print_activities(args, lambda a: ((a.get("responsible") or {}).get("email") or "").lower() == email)


def cmd_buscar(args):
    wanted = tokens(args.texto) or [normalize(args.texto)]
    print_activities(args, lambda a: all(w in normalize(a["title"]) for w in wanted))


def cmd_status_list(args):
    ctx = account_context(args)
    for s in activity_statuses(ctx):
        print("%s | %s" % (s["id"], s["statusName"]))


def cmd_comment(args):
    project_arg, number = split_task_args(args.args)
    ctx = project_context(args, project_arg)
    hit, _ = single_activity(ctx, number)
    fields = [
        "accountId: %d" % ctx["account"],
        "id: %d" % int(hit["id"]),
        'object: "%s"' % esc(args.object),
        'content: "%s"' % esc(args.text),
    ]
    by = args.by or get_current_email()
    if by:
        fields.append('createdBy: "%s"' % esc(by))
    q = "mutation{ createComment(%s) { id content createdAt } }" % ", ".join(fields)
    run_mutation(args, ctx, q, "createComment", "comentar em %s (%s)" % (hit["title"], hit["id"]))


def cmd_set_status(args):
    project_arg, number = split_task_args(args.args)
    ctx = project_context(args, project_arg)
    hit, _ = single_activity(ctx, number)
    status = resolve_status(ctx, args.status)
    q = ("mutation{ changeCustomStatusActivity(id: %d, accountId: %d, folderId: %d, customStatusId: %d, status: %s) "
         "{ id title status customStatus { id statusName } } }"
         % (int(hit["id"]), ctx["account"], ctx["cfg"]["folderId"], int(status["id"]),
            "true" if str(status.get("status")).lower() == "true" else "false"))
    current = (hit.get("customStatus") or {}).get("statusName") or "-"
    run_mutation(args, ctx, q, "changeCustomStatusActivity",
                 "%s (%s): situação %s → %s" % (hit["title"], hit["id"], current, status["statusName"]))


def cmd_update(args):
    project_arg, number = split_task_args(args.args)
    ctx = project_context(args, project_arg)
    hit, acts = single_activity(ctx, number)
    changes = []
    fields = [
        "id: %d" % int(hit["id"]),
        'title: "%s"' % esc(args.title or hit["title"]),
        "accountId: %d" % ctx["account"],
        "folderId: %d" % ctx["cfg"]["folderId"],
    ]
    if args.title:
        changes.append("título → " + args.title)
    if args.responsible:
        user = resolve_user(acts, args.responsible)
        fields.append("responsibleId: %d" % int(user["id"]))
        changes.append("responsável → " + user["name"])
    if args.start:
        fields.append('estimatedStart: "%s"' % esc(args.start))
        changes.append("início previsto → " + args.start)
    if args.end:
        fields.append('estimatedEnd: "%s"' % esc(args.end))
        changes.append("fim previsto → " + args.end)
    if args.percent is not None:
        fields.append("completedPercent: %s" % float(args.percent))
        changes.append("percentual → %s" % args.percent)
    if not changes:
        fail({"error": "nenhum campo informado para alterar"})
    by = get_current_email()
    if by:
        fields.append('updatedBy: "%s"' % esc(by))
    q = ("mutation{ updateActivity(%s) { id title estimatedStart estimatedEnd completedPercent "
         "responsible { name } } }" % ", ".join(fields))
    run_mutation(args, ctx, q, "updateActivity", "%s (%s): %s" % (hit["title"], hit["id"], "; ".join(changes)))


def build_parser():
    p = argparse.ArgumentParser(description="Cliente CLI pra API GraphQL do Artia")
    p.add_argument("--org", default=DEFAULT_ORG, help="OrganizationId (header). Default: %(default)s")
    p.add_argument("--email", help="Override de ARTIA_EMAIL")
    p.add_argument("--password", help="Override de ARTIA_PASSWORD")
    sub = p.add_subparsers(dest="cmd", required=True)

    sc = sub.add_parser("set-credentials", help="grava ~/.config/artia/credentials.json (0600), sem passar senha por argumento")
    sc.add_argument("--email", help="se omitido, pergunta interativamente")
    sc.set_defaults(func=cmd_set_credentials)

    sub.add_parser("login").set_defaults(func=cmd_login)

    w = sub.add_parser("whoami")
    w.set_defaults(func=cmd_whoami)

    pr = sub.add_parser("projects")
    pr.add_argument("--account", required=True)
    pr.set_defaults(func=cmd_projects)

    f = sub.add_parser("folders")
    f.add_argument("--account", required=True)
    f.add_argument("--page", type=int)
    f.set_defaults(func=cmd_folders)

    a = sub.add_parser("activities")
    a.add_argument("--folder", required=True)
    a.add_argument("--account")
    a.add_argument("--mine", action="store_true")
    a.set_defaults(func=cmd_activities)

    t = sub.add_parser("time-entries")
    t.add_argument("--account", required=True)
    t.add_argument("--folder")
    t.add_argument("--activity", required=True)
    t.add_argument("--date", help="YYYY-MM-DD, filtra no cliente")
    t.set_defaults(func=cmd_time_entries)

    c = sub.add_parser("create-entry")
    c.add_argument("--account", required=True)
    c.add_argument("--activity", required=True)
    c.add_argument("--date", required=True, help="YYYY-MM-DD")
    c.add_argument("--start", required=True, help="HH:MM")
    c.add_argument("--duration", required=True, help="horas, ex: 1.5")
    c.add_argument("--status", required=True, help="timeEntryStatusId")
    c.add_argument("--by", help="email ou id de quem registrou (createdBy)")
    c.add_argument("--kind", default="normal", choices=["normal", "extra"])
    c.add_argument("--obs", default="", help="observação do apontamento")
    c.add_argument("--custom-field", action="append", help="HASH=VALOR, repetível")
    c.add_argument("--yes", action="store_true", help="sem isso, só mostra o payload (dry-run)")
    c.set_defaults(func=cmd_create_entry)

    r = sub.add_parser("raw")
    r.add_argument("--query", required=True, help="query/mutation GraphQL completa")
    r.add_argument("--noauth", action="store_true")
    r.set_defaults(func=cmd_raw)

    sub.add_parser("projetos", help="id | nome dos projetos do grupo de trabalho").set_defaults(func=cmd_projetos)

    cd = sub.add_parser("card", help="card da tarefa em texto: descrição + comentários")
    cd.add_argument("args", nargs="+", metavar="[projeto] tarefa")
    cd.set_defaults(func=cmd_card)

    mi = sub.add_parser("minhas", help="tarefas em que sou responsável")
    mi.add_argument("projeto", nargs="*")
    mi.add_argument("--abertas", action="store_true")
    mi.set_defaults(func=cmd_minhas)

    bu = sub.add_parser("buscar", help="tarefas cujo título contém o texto")
    bu.add_argument("texto")
    bu.add_argument("projeto", nargs="*")
    bu.add_argument("--abertas", action="store_true")
    bu.set_defaults(func=cmd_buscar)

    sub.add_parser("status-list", help="id | nome das situações de atividade").set_defaults(func=cmd_status_list)

    co = sub.add_parser("comment", help="comenta na tarefa — sem --yes só mostra o payload")
    co.add_argument("args", nargs="+", metavar="[projeto] tarefa")
    co.add_argument("--text", required=True)
    co.add_argument("--object", default="activity")
    co.add_argument("--by")
    co.add_argument("--yes", action="store_true")
    co.set_defaults(func=cmd_comment)

    ss = sub.add_parser("set-status", help="muda a situação da tarefa — sem --yes só mostra o payload")
    ss.add_argument("args", nargs="+", metavar="[projeto] tarefa")
    ss.add_argument("--status", required=True, help="nome ou id da situação")
    ss.add_argument("--yes", action="store_true")
    ss.set_defaults(func=cmd_set_status)

    up = sub.add_parser("update", help="altera campos da tarefa — sem --yes só mostra o payload")
    up.add_argument("args", nargs="+", metavar="[projeto] tarefa")
    up.add_argument("--title")
    up.add_argument("--responsible", help="email ou nome")
    up.add_argument("--start", help="YYYY-MM-DD")
    up.add_argument("--end", help="YYYY-MM-DD")
    up.add_argument("--percent", type=float)
    up.add_argument("--yes", action="store_true")
    up.set_defaults(func=cmd_update)

    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    args.func(args)
