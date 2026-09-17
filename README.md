# plow-context-layer

Camada de governanca de contexto para agentes. Roda ao lado de um Hermes
existente; nao substitui nada e nao escreve na configuracao de ninguem.

O que ela faz: decide **o que entra no contexto** do agente e dos subagentes,
em vez de despejar tudo. Menos token por sessao, e o instrumento junto para
voce medir o proprio ganho em vez de acreditar num numero alheio.

## Estado

Primeiro tijolo posto: a porta que alcanca o subagente, medida e verificada.

## Achados medidos (2026-09-17, Claude Code 2.1.274)

Hook `PreToolUse` no tool `Agent`, mesma sonda, so trocando o mecanismo:

| mecanismo | despachante | dentro do subagente |
|---|---|---|
| `additionalContext` | PRESENTE | **AUSENTE** |
| `updatedInput` | PRESENTE | **PRESENTE** |

Por isso `hooks/agent-route.py` reescreve `tool_input["prompt"]`: e a unica das
duas portas que atravessa. O contraste entre as duas linhas e o proprio
controle -- mesmo harness, mesmo prompt, so o mecanismo muda.

Heranca de hook por subagente, com controle positivo aceso e negativo limpo
nas duas rodadas:

| origem do hook | evento | alcanca subagente |
|---|---|---|
| `settings.json` do usuario | `PostToolUse:Bash` | **sim** |
| `settings.json` do usuario | `PreToolUse:Edit` | **sim** |
| `settings.json` do usuario | `UserPromptSubmit` | nao (subagente nao tem prompt de usuario) |
| plugin de terceiro | `PreToolUse:Write` | **nao** |

A ultima linha e o buraco que esta camada fecha: gate declarado por plugin nao
vale dentro de subagente. Amostra de um plugin -- generalizar exige mais.

## Cuidados que vieram de erro observado, nao de teoria

- **Hook que quebra BLOQUEIA a ferramenta.** Um hook com shebang errado barrou
  duas chamadas `Agent` seguidas. Por isso `main()` engole toda excecao.
- **Config dir portatil nao isola sozinho.** Com `CLAUDE_CONFIG_DIR` apontado
  para outro lugar, o Claude Code ainda leu o `.claude/settings.json` do
  diretorio de trabalho. Quem empacota precisa controlar o cwd.
- **Ausencia de saida de hook nao prova que ele nao rodou.** Hook que so fala
  quando ha discordancia fica mudo em concordancia. Afirmar ausencia sem
  controle positivo aceso e como nao ter medido.

## Requisitos

- `python3` 3.6+ (sem anotacao PEP 604, de proposito: falha de sintaxe no
  carregamento do modulo acontece ANTES do try/except e bloquearia a ferramenta)
- `claude` no PATH, so para o teste fim-a-fim

Medido: a base `redis:8` (debian) **nao tem python3**. Qualquer imagem que
carregue esta camada precisa instalar o interpretador -- e o
`agent_index_client.py` da Plow precisa dele tambem.

## Verificacao

```
./reproduzir.sh          # autoteste (16 casos) + fim-a-fim
```

O fim-a-fim carrega este proprio diretorio via `--plugin-dir`, despacha um
subagente e confirma que a marca chegou no prompt dele. Se `claude` nao estiver
no PATH, ele pula e avisa -- nao finge que passou.

## Licenca

MIT -- ver `LICENSE`.
