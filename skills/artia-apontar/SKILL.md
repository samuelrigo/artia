---
name: artia-apontar
description: Lê commits git de um período, agrupa por tarefa/dia, estima horas, casa cada grupo com a atividade certa no Artia (API GraphQL) e cria os apontamentos — sempre com tabela de revisão e confirmação antes de gravar. Nunca roda sozinha (só via /artia-apontar).
argument-hint: "[hoje|ontem|YYYY-MM-DD|YYYY-MM-DD..YYYY-MM-DD|7d] [repo...] [--dry-run]"
disable-model-invocation: true
---

# /artia-apontar — apontar horas no Artia a partir do git

Ferramenta: `~/.claude/skills/artia/artia.py` (cliente compartilhado com as skills `artia` e
`artia-planejar`; nos exemplos abaixo, `artia.py` é esse caminho), stdlib-only, sem dependências.
Cliente CLI pra API GraphQL do Artia (`https://api.artia.com/graphql`).

**Regra de ouro: nunca chamar `create-entry --yes` sem o usuário ter confirmado
explicitamente a tabela final.** Sem `--yes`, `create-entry` só mostra o payload
(dry-run) e não grava nada — é o comportamento padrão de segurança do script,
não contorne isso.

## 0. Config

Lê `~/.config/artia/config.json` (fora de qualquer repo git, nunca commitado).
Estrutura: `createdBy`, `authorMatch` (lista de nomes/emails do autor no git log),
`defaults` (`periods`, `timeEntryStatusId`,
`kindOfHours`), `repos` (mapa `caminho_absoluto -> {organizationId, accountId,
folderId}`). Exemplo em `config.example.json` deste diretório.

**Passe `--org <organizationId>`** (o organizationId do repo, do config) nos comandos
abaixo. Sem isso, `artia.py` usa o organizationId do config quando todos os repos
têm o mesmo; com mais de um (ou config vazio) cai no default `1` — quase certamente
errado — e a API retorna erros confusos tipo `"Comunidade desconhecida"` ou
`"Organização não encontrada"` mesmo com accountId/folderId corretos.

Se `organizationId`/`accountId`/`folderId` de algum repo estiverem `null`: pare
e peça pro usuário descobrir:
- `organizationId`: aparece na URL do próprio perfil do usuário no Artia —
  `https://app.artia.com/users/edit?organization_id=<ESSE_NÚMERO>`.
- `accountId` (grupo de trabalho): aparece na URL de qualquer atividade —
  `https://app.artia.com/a/<accountId>/f/<folderId>/activities/<activityId>`.
- `folderId`: com organizationId e accountId em mãos, rode
  `python3 artia.py --org <organizationId> folders --account <accountId>` (ou
  `projects --account <accountId>` — "Projetos" costuma ser o nível certo pra
  `folderId` de `listingActivities`, "Pastas" pode ser um nível acima sem
  atividades diretas).

Depois de descoberto, edite `~/.config/artia/config.json` direto.

`defaults.timeEntryStatusId` é o status de workflow/aprovação do apontamento
(ex: "Em validação", "Aprovado") — não é customizável por `listingCustomStatus`
com filtro `statusObject: "TimeEntry"` direto (retorna vazio mesmo quando o id
certo existe). Jeito confiável de achar o valor certo: peça pro usuário criar
1 apontamento manual pela UI do Artia, depois rode `time-entries` pra essa
atividade/data e leia o `timeEntryStatusId` que veio de verdade.

## 1. Credenciais

Mecanismo primário: `~/.config/artia/credentials.json` (modo 0600, texto
plano — mesmo modelo de `~/.aws/credentials`/`~/.netrc`). `artia.py` lê esse
arquivo automaticamente em todo subcomando que precisa autenticar; env var
`ARTIA_EMAIL`/`ARTIA_PASSWORD` continua funcionando como override, com
prioridade sobre o arquivo, mas **não é o caminho recomendado**: cada chamada
do Bash tool abre um shell não-interativo novo, que não lê `~/.bashrc`, então
`export` feito numa chamada não sobrevive pra próxima — é isso que causa
"exportei mas continua pedindo credencial".

Se `~/.config/artia/credentials.json` não existir e não houver env var
setada: peça pro usuário rodar, **no terminal real dele** (não colar no
chat):
```
python3 artia.py set-credentials --email <email>
```
Isso pede a senha via prompt sem eco (`getpass`) e grava o arquivo 0600.
Depois disso, todo subcomando (`login`, `activities`, `create-entry`, etc)
autentica sozinho sem precisar de env var nenhuma.

**Nunca** peça pra colar a senha no chat, nunca passe `--password` como
argumento de linha de comando (fica no histórico do shell e em `ps`), nunca
imprima o conteúdo de `credentials.json` nem de `ARTIA_PASSWORD`. `artia.py
login` cacheia só o token JWT (extraído o `exp` do próprio token) em
`~/.cache/artia/token.json`, modo 0600, curta duração — os outros
subcomandos reusam esse cache e só re-logam se expirado.

## 2. Parse de `$ARGUMENTS`

- Período: `hoje` (default) | `ontem` | `YYYY-MM-DD` | `YYYY-MM-DD..YYYY-MM-DD` | `Nd` (últimos N dias).
- Repos: paths opcionais; default = todas as chaves de `repos` no config que
  tiverem commit no período.
- `--dry-run`: roda tudo, mostra a tabela, mas nunca chega a perguntar
  confirmação de postagem (fica implícito que não posta).

## 3. Coleta de commits e intervalo de cada tarefa

Por repo, autor batendo com `authorMatch`:
```
git -C <repo> log --since=<...> --until=<...> --date=short \
  --pretty='%H|%ad|%D|%s' --author='<regex dos authorMatch, OR-separado>'
```
Agrupe por `(repo, branch)` — **não** por dia isolado (commit é raro e pode
passar dias entre um e outro na mesma tarefa). Branch vem de `%D` (ref-names)
quando presente; senão usa a branch atual do repo (`git branch --show-current`)
como fallback — é heurística, o usuário revisa/corrige na tabela do passo 6.

Pra cada `(repo, branch)`, ache o primeiro e o último dia com commit real
(min/max). Isso é o "intervalo da tarefa".

## 4. Resolução da atividade (match por título)

Uma vez por `(repo, branch)` — não recalcule por dia — (repo já resolve
`organizationId`/`accountId`/`folderId` via config):
```
python3 artia.py --org <organizationId> activities --folder <folderId> --account <accountId> --mine
```
Normalize (minúsculas, sem acento) o nome da branch (sem prefixo `feat/`,
trocando `-`/`_` por espaço) e os assuntos dos commits do grupo. Compare por
overlap de tokens contra `title` de cada atividade retornada. Guarde as top-3
por score — não decida sozinho quando o placar for ambíguo (top-1 e top-2
muito próximos): deixe as 3 visíveis na tabela pro usuário escolher. Todos os
dias que essa branch acabar preenchendo (passo 5) herdam essa mesma escolha.

## 5. Preenchendo o calendário (por período, nunca dia cheio)

O dia de trabalho tem **2 períodos fixos**, em `defaults.periods` (padrão
`08:00–12:00` = 4h e `13:30–18:00` = 4,5h; o intervalo entre eles é almoço).
**Nunca** crie um apontamento único cobrindo o dia todo, nem um que atravesse o
almoço: cada apontamento cabe inteiro dentro de um período, com início e
duração tirados dele. Não estima por intervalo entre commits. Pra cada dia
**útil** (segunda a sexta) dentro do período pedido:

1. Liste quais `(repo, branch)` tiveram commit **real** nesse dia, na ordem do
   primeiro commit.
2. Distribua as tarefas entre os períodos, em ordem: 1 tarefa ocupa manhã e
   tarde; com 2 ou mais, cada período recebe um grupo contíguo de tarefas
   proporcional à sua duração (2 tarefas: uma de manhã, outra à tarde; 3: uma de
   manhã, duas à tarde; 4: duas e duas).
3. Dentro do período, divida o tempo igualmente entre as tarefas do grupo, em
   blocos de 30 min, um após o outro. Nunca dois apontamentos no mesmo horário.
4. Mesma regra do `diario.py` (`dividir_horarios`): use-o como referência.
5. **Nenhum commit real** nesse dia, mas ele cai **entre** o primeiro e o
   último dia de commit de uma única branch, e nenhuma outra branch tem
   commit nesse intervalo → preenche manhã e tarde com a mesma branch. É o
   caso "commitei segunda, só commitei de novo quinta, mas trabalhei terça e
   quarta na mesma tarefa".
6. Não se encaixa em nada acima (fim de semana, fora do intervalo de
   qualquer branch, ou gap ambíguo — duas branches diferentes cercando o
   buraco, sem como saber qual continuou) → **não** propõe nada nesse dia.
   Ambiguidade não se resolve advinhando.

Isso é **estimativa de partida** — o usuário edita os números no passo 6.

## 6. Observação (padrão dos apontamentos reais)

Texto curto, minúsculo, sem ponto final e sem número da tarefa — diz a fase,
não o conteúdo do commit:

| evidência | observação |
|---|---|
| PR aberta no dia | `implementação e abertura de pr` |
| push depois de review recebida na PR | `implementando mudanças solicitadas na pr` |
| review/comentário em PR | `revisão de comentários da pr` |
| push sem PR | `planejamento e implementação da atividade` |
| sem código (branch criada, comentário no card) | `análise e planejamento da atividade` |
| dia preenchido sem commit (teste) | `teste da <funcionalidade> [no app/no web]` |

## 6.1 Dedupe + tabela de proposta

Antes de mostrar, rode dedupe contra as **3 candidatas** do grupo (não só a
top-1) — se o match errar a atividade certa, checar só a top-1 deixa passar
duplicata numa atividade diferente da que já tem entrada real:
```
python3 artia.py --org <organizationId> time-entries --account <accountId> --folder <folderId> --activity <activityId> --date <data>
```
Se **qualquer uma** das 3 já tiver entrada nesse dia: marque a linha como **já
apontado** (mostra `id`/`duration`/qual atividade já tem), e trate isso como
sinal extra de qual candidata é a certa (se foi a #2 ou #3 que já tinha
entrada, é forte indício de que o match top-1 errou). Não proponha criar de
novo a menos que o usuário peça explicitamente.

Monte e mostre uma tabela markdown:

| data | repo/branch | atividade (top-3 candidatas) | início | horas | observação | status |
|---|---|---|---|---|---|---|
| 2026-07-22 | loja-web / feat/tarefa21-... | **1000001 — Tarefa 21 - Exportar relatório** / 1000002 / 1000003 | 08:00 | 4 | feat: permissão de distribuição manual... | novo |
| 2026-07-22 | loja-web / feat/tarefa21-... | (mesma de cima) | 13:30 | 4.5 | feat: permissão de distribuição manual... | novo |
| 2026-07-23 | loja-web / feat/tarefa21-... | (mesma de cima, dia sem commit — preenchido, ver passo 5.5) | 08:00 | 4 | — | novo |
| 2026-07-23 | loja-web / feat/tarefa21-... | (mesma de cima, dia sem commit — preenchido, ver passo 5.5) | 13:30 | 4.5 | — | novo |

Todo dia vira **pelo menos 2 linhas** (manhã e tarde), cada uma com sua
atividade, `início` e `horas`. Se o usuário editar horas ou início, a linha
continua tendo que caber dentro de um dos períodos.

## 7. Edição + confirmação

Peça pro usuário revisar: pode trocar a atividade escolhida (entre as
candidatas ou outro id), ajustar horas, editar observação, remover linhas.

**Se o usuário trocar a atividade de alguma linha, refaça o dedupe do passo 6
pra essa nova `(activityId, data)` antes de seguir** — o cheque anterior foi
feito só pra atividade top-1 original, não cobre a troca.

**Só prossiga para o passo 8 depois de uma confirmação explícita** ("pode
postar", "confirma", etc). Em modo `--dry-run`, pare aqui.

## 8. Postagem

Por linha confirmada:
```
python3 artia.py --org <organizationId> create-entry --account <accountId> --activity <activityId> \
  --date <data> --start <início da linha> --duration <horas da linha> \
  --status <defaults.timeEntryStatusId> --by <createdBy> --kind normal \
  --obs "<observação>" --yes
```
`início` e `horas` vêm da linha da tabela (passo 5), sempre dentro de um dos
`defaults.periods` — um `create-entry` por linha, nunca um só pro dia.
Reporte os `id` criados numa lista curta. Se algum `timeEntryStatusId` da
config estiver incorreto pro fluxo real do usuário (API rejeitar), pare,
mostre a mensagem de erro decisiva (não o JSON inteiro) e peça o id certo —
não adivinhe tentando valores em sequência.

Se o cwd for um repo com `.wolf/` (protocolo OpenWolf do projeto): erro real
de API/gravação vira entrada em `.wolf/buglog.json` daquele repo, como
qualquer outro bug.

## Referência rápida da API (GraphQL, `https://api.artia.com/graphql`)

Toda request autenticada leva `Authorization: Bearer <token>` +
`OrganizationId: <organizationId>`. `artia.py` monta esse header sozinho, mas
só com o valor certo se você passar `--org <organizationId>` — sem isso ele
cai no organizationId do config (ou em `1` se o config tiver mais de um).

- `authenticationByEmail(email, password){ token }`
- `listingProjects(accountId){ id, name, status, costCenterId }`
- `listingFolders(accountId, page){ id, name, status, folderTypeName }`
- `listingActivities(accountId, folderId){ id, title, status, responsible{email} }`
- `listingTimeEntries(accountId, folderId, activityId){ id, dateAt, duration, observation, timeEntryStatusId }`
- `createTimeEntry(accountId, activityId, dateAt, startTime, duration, timeEntryStatusId, createdBy, kindOfHours, observation, customField){ id, ... }`

Qualquer outra operação da coleção "Developers - Artia" (Postman) que não
tenha subcomando dedicado: use `artia.py raw --query '...'` com a query/mutation
completa (mesma sintaxe inline usada nos exemplos acima, sem `variables`).
