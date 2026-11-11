---
name: artia
description: Operações genéricas no Artia sem apontar horas — ler o card de uma tarefa (descrição + comentários), listar minhas tarefas, buscar tarefa por texto, listar projetos e situações, comentar, mudar situação, alterar responsável/datas/percentual. Use em "lê a tarefa 60 do vendas", "minhas tarefas abertas", "comenta na tarefa 61", "muda a 60 pra em andamento". Apontar horas é /artia-apontar; plano de implementação é /artia-planejar.
argument-hint: "<pedido em texto livre, ex.: lê a tarefa 60 do vendas>"
context: fork
model: haiku
allowed-tools: Bash(python3 ~/.claude/skills/artia/artia.py *)
---

# /artia — consulta e edição de tarefas do Artia

Pedido: $ARGUMENTS

Ferramenta única: `python3 ~/.claude/skills/artia/artia.py <subcomando>`. Organização e
grupo de trabalho vêm de `~/.config/artia/config.json`; não passe `--org`.
`[projeto]` é um pedaço do nome (`vendas`, `financeiro`) ou o id; omitido,
vale o projeto do diretório atual. `<tarefa>` é o número do título `Tarefa N`.

| pedido | comando |
|---|---|
| ler tarefa | `card [projeto] <tarefa>` |
| minhas tarefas | `minhas [projeto] [--abertas]` |
| achar tarefa por texto | `buscar "<texto>" [projeto] [--abertas]` |
| projetos | `projetos` |
| situações possíveis | `status-list` |
| comentar | `comment [projeto] <tarefa> --text "<texto>"` |
| mudar situação | `set-status [projeto] <tarefa> --status "<nome ou id>"` |
| alterar campos | `update [projeto] <tarefa> [--responsible <nome/email>] [--start AAAA-MM-DD] [--end AAAA-MM-DD] [--percent N] [--title "..."]` |
| outra leitura | `raw --query '<query GraphQL>'` (só `query`, nunca `mutation`) |

## Regras

- Rode o mínimo de comandos: um por pedido quando der. Não explore além do pedido.
- Leitura: devolva a saída do comando **na íntegra**, sem resumir, reescrever ou cortar
  descrição, critérios de aceite e comentários. Listas saem como vieram.
- Erro em JSON com `projetos`, `candidatas`, `situacoes` ou `candidatos`: devolva o erro
  e a lista. Não escolha por conta própria nem tente outro valor.
- Escrita (`comment`, `set-status`, `update`): **nunca use `--yes`**. Rode sem ele
  (dry-run) e devolva exatamente:
  ```
  AÇÃO PENDENTE DE CONFIRMAÇÃO
  <linha DRY-RUN impressa pelo comando>
  comando: <comando completo que você rodou> --yes
  ```
  Quem confirma com o usuário e roda com `--yes` é a conversa principal.
- `Faltam credenciais`: devolva a instrução de rodar no terminal do usuário
  `python3 ~/.claude/skills/artia/artia.py set-credentials --email <email>`. Nunca peça
  senha, nunca leia `~/.config/artia/credentials.json`.
- Pedido de apontar horas → responda que é `/artia-apontar`. Plano de implementação →
  `/artia-planejar`.
