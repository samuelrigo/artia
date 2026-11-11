# artia

Skills do [Claude Code](https://claude.com/claude-code) para o
[Artia](https://artia.com), sobre um cliente CLI único (`artia.py`, só stdlib).

Nada aqui é software oficial do Artia. É um cliente independente da API
GraphQL pública deles (`https://api.artia.com/graphql`).

| skill | para quê | modelo |
|---|---|---|
| [`/artia`](./skills/artia/SKILL.md) | ler card, listar e buscar tarefas, comentar, mudar situação e campos | subagente Haiku (`context: fork`) |
| [`/artia-apontar`](./skills/artia-apontar/SKILL.md) | apontar horas a partir do `git log`, com tabela de revisão | sessão, só manual |
| [`/artia-planejar`](./skills/artia-planejar/SKILL.md) | plano de implementação a partir do card, cruzado com o código | sessão |

## Instalação

```bash
git clone <este repo> ~/dev-pessoal/artia
~/dev-pessoal/artia/install.sh
```

`install.sh` cria symlinks em `~/.claude/skills/` para as três skills. Pasta
que já existir lá com o mesmo nome é movida para
`~/.claude/skills-backup-<data>/`, não apagada. O cliente fica em
`~/.claude/skills/artia/artia.py`.

Config:

```bash
mkdir -p ~/.config/artia
cp config.example.json ~/.config/artia/config.json
python3 ~/.claude/skills/artia/artia.py set-credentials --email seu@email
```

`~/.config/artia/config.json`:

- `authorMatch`: nomes/emails que aparecem no seu `git log` como autor
- `repos`: caminho absoluto de cada repositório → `organizationId`,
  `accountId`, `folderId`:
  - `organizationId`: URL do seu perfil —
    `https://app.artia.com/users/edit?organization_id=<ESSE_NÚMERO>`
  - `accountId` (grupo de trabalho) e `folderId` (projeto): URL de qualquer
    atividade —
    `https://app.artia.com/a/<accountId>/f/<folderId>/activities/<activityId>`
- `defaults.dailyHours`/`morningStart`/`afternoonStart`: jornada usada pelo
  `/artia-apontar`
- `defaults.timeEntryStatusId`: crie 1 apontamento manual na UI e leia o valor
  com `artia.py time-entries --account <ID> --activity <ID>`

Com um único `organizationId` no config, `--org` é opcional. Com mais de um,
passe `--org <organizationId>` antes do subcomando.

## Uso

```
/artia lê a tarefa 60 do vendas
/artia minhas tarefas abertas
/artia muda a 60 do vendas pra em andamento
/artia-apontar ontem --dry-run
/artia-planejar vendas 60
```

## `artia.py`

Saída em texto compacto (uma linha por item), pensada para gastar pouco token:

```
artia.py projetos
artia.py card [projeto] <tarefa>
artia.py minhas [projeto] [--abertas]
artia.py buscar "<texto>" [projeto] [--abertas]
artia.py status-list
artia.py comment [projeto] <tarefa> --text "..." [--yes]
artia.py set-status [projeto] <tarefa> --status "<nome|id>" [--yes]
artia.py update [projeto] <tarefa> [--responsible X] [--start D] [--end D] [--percent N] [--title T] [--yes]
```

`[projeto]` é um pedaço do nome ou o id; omitido, vale o projeto do diretório
atual. `<tarefa>` é o número do título `Tarefa N`.

Saída em JSON, usada pelo `/artia-apontar`:

```
artia.py login | whoami
artia.py projects --account <ID>
artia.py folders --account <ID>
artia.py activities --folder <ID> --account <ID> --mine
artia.py time-entries --account <ID> --activity <ID> --date 2026-07-22
artia.py create-entry --account <ID> --activity <ID> --date D --start 09:00 --duration 1.5 --status <ID> [--yes]
artia.py raw --query '...'
```

## Segurança

- Toda escrita (`comment`, `set-status`, `update`, `create-entry`) exige
  `--yes`; sem ele só mostra o payload. A skill `/artia` roda em dry-run e
  devolve o comando para a conversa principal confirmar com o usuário.
- Credenciais em `~/.config/artia/credentials.json` (modo `0600`), gravadas por
  `set-credentials` com prompt sem eco. Senha nunca vai por argumento nem pelo
  chat. `ARTIA_EMAIL`/`ARTIA_PASSWORD` no ambiente têm prioridade.
- Só o token JWT é cacheado (`~/.cache/artia/token.json`, modo `0600`).
- `~/.config/artia/` fica fora de qualquer repositório git.

## Licença

MIT — veja [`LICENSE`](./LICENSE).
