---
name: artia-planejar
description: Lê o card de uma tarefa do Artia (projeto do Artia + número da tarefa), decide quais repos (back, web, mobile) a tarefa toca, cruza com o código e o estado das branches e escreve o plano de implementação para aprovação — sem codar. Use em "/artia-planejar vendas 60", "planeja a tarefa 233 do financeiro".
argument-hint: "[projeto-do-artia] <número-da-tarefa>"
---

# /artia-planejar — plano de implementação a partir do card do Artia

Ferramenta: `card.py` (neste diretório), stdlib-only. Reusa `artia.py` da skill
`artia` (`~/.claude/skills/artia/`) para login, token em cache e
GraphQL — mesma config (`~/.config/artia/config.json`) e mesmas credenciais
(`~/.config/artia/credentials.json`). Tudo que a `artia-apontar` diz sobre
credenciais vale aqui: nunca pedir senha no chat, nunca imprimir o arquivo de
credenciais, se faltar credencial pedir pro usuário rodar no terminal dele
`python3 ~/.claude/skills/artia/artia.py set-credentials --email <email>`.

**Esta skill só planeja.** Não cria branch, não edita código, não commita, não
abre PR. Termina com o plano escrito e a pergunta "posso começar?".

## 1. Argumentos

`$ARGUMENTS` = `[projeto-do-artia] <tarefa>`.

- `projeto-do-artia`: nome ou pedaço do nome do projeto no Artia (`vendas`,
  `financeiro`) ou o id dele (`1234567`).
  Omitido: deduzido pelo cwd quando ele está dentro de um repo do config.
- `tarefa`: número do título `Tarefa N - ...` (`60`, `T60`, `060`).
- **Não** se passa repo do GitHub: a tarefa pode tocar back, web e mobile ou só
  um deles, e quem decide isso é o passo 3.
- Faltou o número: pergunte. Não adivinhe.

## 2. Ler o card

```
python3 ~/.claude/skills/artia-planejar/card.py [projeto] <tarefa> [--with-siblings]
```

Saída JSON: `projeto_artia`, `url`, `id`, `title`, `situacao`, `responsavel`,
`descricao` (HTML já em texto), `comentarios`, `partes_irmas` (automático quando
o título termina em `Parte N`) e `local`:
- `local.origem = "config"`: `local.subrepos` são os repos git do projeto
  (os do config + os irmãos na mesma pasta, tirando os que pertencem a outro
  projeto do Artia e só dividem a pasta).
- `"palpite pelo nome"`: achou pasta em `~/dev` pelo nome do projeto — confirme
  com o usuário antes de usar.
- `"nao encontrado"`: pergunte onde está o código e ofereça adicionar os repos em `repos` do config com
  `organizationId`/`accountId`/`folderId` = `artia` da saída.

Erros saem em JSON no stdout (`error` + `projetos` ou `candidatas`): projeto
ambíguo/desconhecido → mostre a lista e pergunte; `nenhuma atividade` → não
invente o card.

- `concluida: true` ou situação "Concluída": avise antes de planejar.
- Partes irmãs: leia o card das irmãs citadas como dependência
  (`card.py <projeto> <número>`), e veja se existe `.wolf/<épico>/PLANO.md` ou
  `CARDS.md` no projeto — épico com handoff próprio segue aquele arquivo.
- Comentários mudam escopo com frequência: leia todos, o mais novo vence.

## 3. Quais repos a tarefa toca

Antes de abrir código, leia no diretório do projeto (pai dos subrepos) e em cada
subrepo: `CLAUDE.md`, `.wolf/STATUS.md`, `.wolf/cerebrum.md` (Do-Not-Repeat
inteiro), `.wolf/anatomy.md`.

Classifique cada subrepo de `local.subrepos` como **muda** / **só confere** /
**fora**, pelo que o card pede:
- tela, filtro, coluna, formulário web → web; quase sempre exige endpoint ou
  query no backend → conferir se o backend já entrega o dado.
- tela/fluxo do app, offline, sincronização → mobile (+ backend se o contrato muda).
- tabela, campo, migration, integração externa, endpoint → backend; DTO que muda
  arrasta as interfaces duplicadas de web e mobile.
- Na dúvida entre "muda" e "só confere", leia o código antes de decidir.

Para cada subrepo **muda** ou **só confere**:
```
git -C <subrepo> fetch --all --prune
git -C <subrepo> branch -a | grep -E "feat/0*<N>([-/]|$)|[-/]<N>-"
gh pr list -R <owner/repo> --state all --search "<N>" --limit 10
```
Branch ou PR com o número já existe: o plano parte dela (`git log --oneline
origin/<base>..origin/<branch>`, `git diff --stat`), não do zero. Nunca
concluir "não existe" olhando só o checkout atual.

Mudanças recentes de outras pessoas (antes de fechar o plano), em cada subrepo
**muda** ou **só confere**:
```
git -C <subrepo> log --all --since=30.days --format='%h %an %ad %D | %s' --date=short \
  --author='^(?!<autor do config authorMatch>)' --perl-regexp | head -30
git -C <subrepo> for-each-ref --sort=-committerdate --format='%(committerdate:short) %(authorname) %(refname:short)' refs/remotes | head
```
Para cada commit/branch alheio recente, `git show --stat` e cruzar com os
arquivos do plano: mesmo arquivo → ver o diff e dizer se é o mesmo trecho ou
outro método; branch nova de fluxo (ex.: `develop`, CD que mudou de gatilho) →
pergunta sobre base de branch/PR. Vai para a seção "Mudanças recentes de outros"
do plano, com veredito por commit.

Achar o código: busca ampla (vários repos/diretórios) → agente `Explore` com o
texto do card, pedindo só `arquivo:linha` + como o fluxo funciona hoje. Leitura
pontual → direto. Ache também a feature parecida já mergeada na `master` (mesma
tela, mesmo tipo de filtro/CRUD) para reaproveitar o padrão — o revisor cobra
código "feito diferente sem motivo".

## 4. Escrever o plano

Destino: `<projeto>/.wolf/tarefas/T<N>-PLANO.md` quando o diretório do projeto
tiver `.wolf/`; senão o scratchpad da sessão. Arquivo já existe: leia e
atualize, não sobrescreva às cegas.

```markdown
# T<N> — <título do card>

Artia: <url> · <projeto_artia> · situação: <situacao> · lido em <data>

## Card
<descrição integral + comentários, sem resumir os critérios de aceite>

## Repos
| repo | papel (muda / só confere / fora) | por quê |

## Hoje
<como o fluxo funciona agora, com arquivo:linha; branches/PRs existentes>

### Mudanças recentes de outros
| commit/branch | o que faz | toca a tarefa? |

## Mudança por repo
### <repo> (um bloco por repo que muda)
- arquivo — o que muda e por quê
- migration nova? (nome, reversível?)
- contrato: endpoint/DTO/interface que muda em mais de um repo

## Critérios de aceite → como verificar
| critério do card | repo/onde é atendido | teste/verificação |

## Ordem, branches e PRs
- ordem entre repos (backend antes de quem consome)
- branch `feat/<N>-<slug>` a partir de `origin/develop` em cada repo que muda; repo sem `develop` no remoto (`git ls-remote --heads origin develop`) → pergunta, não cair na `master` calado
- `develop` atrás da `master`: listar `git log origin/develop..origin/master` e dizer se algum desses commits toca os arquivos do plano
- dependência não mergeada entra por `git merge origin/<dep>`; base da PR = `develop`
- PR por entrega testável (no mínimo uma por repo quando atravessa back/web/app)
- título de PR `feat/<N>: descrição em minúsculas`; commit `feat: ...`

## Riscos e perguntas em aberto
```

Regras de código do time: se existir `~/.config/artia/planejar-regras.md`, leia e
faça o plano respeitar cada uma (são cobradas na revisão). Confira também o
`cerebrum.md` e as memórias do projeto, que podem ter regra mais nova.

## 5. Entrega

No chat: caminho do arquivo, tabela curta de repos (muda / só confere), resumo
em 5–10 linhas (nº de branches/PRs, migration sim/não, perguntas em aberto) e a
pergunta se pode começar. Pergunta que bloqueia a implementação vai via
`AskUserQuestion`, não enterrada no arquivo.

Depois: linha em `.wolf/memory.md` e, se o plano revelou convenção nova, entrada
no `.wolf/cerebrum.md`.
