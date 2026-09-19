# plow-context-layer

[![CI](https://github.com/Two2Bac-git/plow-context-layer/actions/workflows/ci.yml/badge.svg)](https://github.com/Two2Bac-git/plow-context-layer/actions/workflows/ci.yml)

Camada de governanca de contexto para agentes. Roda ao lado de um Hermes
existente, nao substitui nada e nao escreve na configuracao de ninguem.

Ela injeta um **contrato de comportamento** no prompt de todo subagente
despachado -- e o contrato muda o que o subagente faz. Medido, nao afirmado.

---

## Instalacao

```sh
git clone https://github.com/Two2Bac-git/plow-context-layer
cd plow-context-layer
./instalar.sh
```

Isso faz duas coisas, e so isso:

1. um symlink `~/.local/bin/plow-uso` -> `bin/emitir-uso.py` deste repo
2. imprime a linha para carregar o plugin

Sem sudo. Sem tocar em `/etc`. Sem escrever no `~/.claude` de ninguem.

```sh
./instalar.sh --check         # verifica pre-requisitos e instalacao
./instalar.sh --desinstalar   # remove SO o symlink, e so se for nosso
./instalar.sh --prefix /opt   # outro destino
```

Carregar no Claude Code:

```sh
claude --plugin-dir /caminho/para/plow-context-layer
```

Permanente:

```sh
echo "alias claude='claude --plugin-dir $PWD'" >> ~/.bashrc
```

### O que ela escreve no seu disco

| caminho | quando | o que |
|---|---|---|
| `$PREFIX/bin/plow-uso` | `./instalar.sh` | um symlink, removivel |
| o `--db` que voce passar | so se voce rodar o emissor | tabela `session_model_usage` |

Nada mais. Desinstalar volta ao estado anterior.

---

## Resultados medidos

### 1. O contrato muda comportamento (A/B, 2026-09-17)

Subagente recebe a tarefa de sobrescrever um arquivo. Mesma tarefa, mesmo
modelo; muda so o plugin estar carregado:

| braco | o subagente disse | o arquivo no disco |
|---|---|---|
| **com** a camada | `RECUSEI` | `ORIGINAL` -- intacto |
| **sem** a camada | `FEITO` | `DESTRUIDO` -- sobrescrito |

O disco e a segunda derivacao: auto-relato de subagente sozinho nao prova nada.
As duas concordam nos dois bracos.

### 2. O gate BLOQUEIA de verdade (A/B isolado)

O contrato da secao 1 persuade. Este hook impede. Para separar os dois, o
braco `com` carregou **so o gate**, sem o contrato injetado -- senao nao daria
para saber qual dos dois agiu. E a tarefa foi trocada por uma banal (esvaziar
um rascunho), que nenhum modelo hesita em executar por conta propria:

| braco | o agente disse | o arquivo no disco |
|---|---|---|
| **com** o gate | `RESULTADO=IMPEDIDO` | 17 B -> 17 B, intacto |
| **sem** o gate | -- | 17 B -> **0 B**, truncado |

E o erro que ele copiou, verbatim:

```
ERRO=Bloqueado pelo contrato desta sessao: pare antes de destruir.
```

Esse texto e o `permissionDecisionReason` deste repo, byte a byte. Nao foi o
modelo julgando: foi o hook negando.

### 3. O gate cobre a ferramenta Write, nao so Bash (A/B)

Subagente instruido a usar a ferramenta `Write` sobre um arquivo existente:

| braco | o agente disse | o arquivo no disco |
|---|---|---|
| **com** o gate | `RESULTADO=IMPEDIDO` | `ORIGINAL`, intacto |
| **sem** o gate | `RESULTADO=FEITO` | `NOVO`, sobrescrito |

`Edit` continua livre de proposito: e cirurgico, e barrar toda edicao tornaria
o agente inutil. Criar arquivo NOVO tambem passa -- nao ha o que destruir.

### 4. So uma das duas portas alcanca o subagente

Hook `PreToolUse` no tool `Agent`, mesma sonda, so trocando o mecanismo:

| mecanismo | despachante | dentro do subagente |
|---|---|---|
| `additionalContext` | PRESENTE | **AUSENTE** |
| `updatedInput` | PRESENTE | **PRESENTE** |

O contraste entre as duas linhas e o proprio controle do experimento -- mesmo
harness, mesmo prompt, so o mecanismo muda. Por isso `hooks/agent-route.py`
reescreve `tool_input["prompt"]`.

### 5. Heranca de hook por subagente

Controle positivo aceso e controle negativo limpo nas duas rodadas:

| origem do hook | evento | alcanca subagente |
|---|---|---|
| `settings.json` do usuario | `PostToolUse:Bash` | **sim** |
| `settings.json` do usuario | `PreToolUse:Edit` | **sim** |
| `settings.json` do usuario | `UserPromptSubmit` | nao (subagente nao tem prompt de usuario) |
| plugin de terceiro | `PreToolUse:Write` | **nao** |

A ultima linha e o buraco que esta camada contorna: gate declarado por plugin
nao vale dentro de subagente.

### 6. O emissor de uso

245 transcripts, 1,6 s. Duas derivacoes independentes, codigos diferentes:

| derivacao | sessoes x modelo | tokens in+out |
|---|---|---|
| `bin/emitir-uso.py` | 88 | 16.648.544 |
| script independente | 88 | 16.648.544 |

### 7. Validado com o codigo da Plow, nao com o nosso

| funcao deles | entrada | resultado |
|---|---|---|
| `_has_usage_table()` | nosso store | `True` |
| `_has_usage_table()` | store vazio (controle negativo) | `False` |
| `from_hermes()` | nosso store, 1a chamada | `{}` + "baseline recorded" |
| `from_hermes()` | nosso store, 2a chamada | `{"2026-09-18": {"claude-opus-5": {...}}}` |

### 8. Instalador

Sete caminhos testados: check antes, instalar, comando funciona, check depois,
idempotencia, **recusa de remover symlink alheio**, desinstalar.

### 9. CI

Matriz `python 3.9 / 3.11 / 3.13`. A matriz nao e decorativa: ela mede a
portabilidade que nao da para medir na maquina do autor, que so tem um python.

---

## Configuracao: o que voce controla

Tudo por variavel de ambiente, lida na CHAMADA. Nenhum arquivo de config,
nenhuma edicao de codigo. Padroes seguros; a mudanca e sempre sua.

| variavel | padrao | efeito |
|---|---|---|
| `PLOW_GATE_MODO` | `aberto` | `fechado` faz o gate barrar quando **nao consegue decidir**, em vez de deixar passar |
| `PLOW_GATE_EXTRA` | vazio | lista de comandos que voce tambem quer barrar: `PLOW_GATE_EXTRA=curl,wget` |
| `PLOW_GATE_PERMITIR` | vazio | lista do que voce assume e libera, inclusive `Write` |

E, antes de depender do mecanismo, voce pode perguntar a ele:

```sh
python3 hooks/gate-destrutivo.py --explicar "rm -rf /tmp/x"
#   VEREDITO  : BLOQUEADO
#     - `rm` apaga arquivos

python3 hooks/gate-destrutivo.py --explicar 'bash -c "rm -rf /x"'
#     - dentro de `bash -c`: `rm` apaga arquivos
```

Sai `0` quando passa e `1` quando barra, entao da para usar em script.

## Erros e armadilhas, documentados

Todos observados durante a construcao. Nenhum e teorico.

**Hook que quebra BLOQUEIA a ferramenta.** Um hook com shebang errado barrou
duas chamadas `Agent` seguidas. Por isso `main()` engole toda excecao e o CI
alimenta 6 entradas podres verificando que nenhuma levanta.

**A primeira execucao do Indice reporta ZERO.** O cliente da Plow reporta
*delta* entre snapshots, nao total. A primeira vez so grava a linha de base:
`"Hermes baseline recorded -- usage is reported from the next run on."` Rode
duas vezes, com uso no meio, ou a pagina do agente nasce zerada e parece
quebrada. Detalhes em `INTEGRACAO.md`.

**Config dir portatil nao isola sozinho.** Com `CLAUDE_CONFIG_DIR` apontado
para outro lugar, o Claude Code ainda leu o `.claude/settings.json` do
diretorio de trabalho. Quem empacota precisa controlar o cwd.

**Ausencia de saida de hook nao prova que ele nao rodou.** Hook que so fala
quando ha discordancia fica mudo quando ha concordancia. Uma sonda apontada
para `echo` deu "ausente" e nao significava nada: `echo` nao dispara regra
nenhuma. Afirmar ausencia sem controle positivo aceso e nao ter medido.

**A janela faz parte do instrumento.** A mesma pergunta em tres janelas:
`-name '*.md'` deu 1816 = 1816 (concordam), `<tudo>` deu 3019 = 3019
(concordam), e `-type f` deu **18 contra 1853** -- 103x de diferenca. Duas
janelas cegas quase desmentiram uma regra que estava certa.

**Anotacao `dict | None` exige Python 3.10+, e falha antes do `try/except`.**
Erro de sintaxe acontece no carregamento do modulo, nao na execucao -- ou seja,
justamente o caso que a promessa "nunca bloqueia a ferramenta" nao cobriria.
Removida; o CI mede isso com a matriz.

**Heredoc aninhado dentro de `$()` quebra em `sh` e em `bash`.** O primeiro
`reproduzir.sh` nasceu assim. `sh -n` pegou antes do commit; o CI roda `sh -n`,
`bash -n` e `shellcheck` nos dois scripts.

**`<agent>` em comando copiado vira redirecionamento.** Colar
`docker exec hermes-<agent>` no shell produz `agent: Arquivo ou diretorio
inexistente`, porque `<` e redirecionamento. Placeholder em bloco de comando
precisa ser substituido antes de colar.

**`--self-check` do cliente da Plow estoura sem token.** O `_post()` deles faz
`json.loads(e.read() or b"{}")` e quebra quando a resposta de erro nao e JSON.
Nao e problema desta camada, mas confunde no primeiro uso.

**Placeholder colado vira credencial invalida, e o erro nao diz isso.** O
bloco de comandos deste repo foi colado inteiro, com `export
PLOW_AGENT_TOKEN=...`, e a Plow devolveu `could not get Plow assertion: 401` --
que nao distingue "token errado" de "token placeholder". Corrigido na origem:
`INTEGRACAO.md` agora usa `read -rsp`, que pede o valor e nao ecoa, e
`bin/preflight.py` classifica o token antes de qualquer chamada.

**Duas fontes de uso coincidentes SOMAM, nao competem.** O
`agent_index_client` coleta de `agentsview` e do store Hermes, e o `merge()`
dele documenta: *"Same (day, model) from two collectors adds up rather than one
winning."* Se `agentsview` estiver instalado, ele ja reporta Claude Code -- e o
emissor deste repo, que escreve Claude Code no store Hermes, faria a mesma
sessao contar duas vezes. Medido com a funcao deles: 100 -> 200, **fator 2.0x**.
`bin/emitir-uso.py` agora **recusa gravar** se encontrar `agentsview` nos tres
caminhos que o proprio cliente procura; `--mesmo-com-agentsview` e a saida
explicita para quem sabe que nao ha sobreposicao.

**Default ligado no `def` nao enxerga quem troca a constante depois.** O
primeiro teste da guarda acima passou verde sem exercitar nada, porque
`def f(x=CONSTANTE)` congela o valor na definicao. A resolucao virou tardia
(`x=None` e `x or CONSTANTE`) e so entao o teste falhou como devia.

**A base `redis:8` (debian) nao tem `python3`.** Qualquer imagem que carregue
esta camada precisa instalar o interpretador -- e o `agent_index_client.py` da
Plow precisa dele tambem.

**O numero se move enquanto voce mede.** Duas leituras do emissor com dois
minutos de diferenca deram 16.648.544 e 16.651.940 tokens. A diferenca eram os
tokens *da propria sessao que media*.

---

## Limites conhecidos

Quatro limites que este README listava foram fechados e viraram teste; o que
segue abaixo e o que **continua** verdadeiro.

- **A cobertura de interpretador e heuristica, nao prova.** `bash -c "rm ..."`
  passa pelo mesmo analisador, recursivamente, e `python3 -c` casa por API
  destrutiva conhecida. Quem ofuscar a string passa. **O gate detem o descuido,
  nao o adversario** -- e essa e a promessa, nao um eufemismo.
- **`Edit` nao e barrado.** Decisao, nao esquecimento: `Edit` e cirurgico, e
  barrar toda edicao tornaria o agente inutil. Quem quiser cobrir tambem:
  `PLOW_GATE_EXTRA` nao alcanca ferramentas, so comandos -- abra uma issue.
- **Config do diretorio de trabalho vaza.** Se houver `.claude/settings.json`
  no diretorio onde voce roda, o Claude Code o carrega junto. O instalador
  **avisa**, mas nao impede: impedir seria decidir pelo seu projeto.
- **"Hook de plugin nao alcanca subagente" tem amostra de um.** Um plugin
  testado. Generalizar exige mais.
- **O CI nao roda o teste fim-a-fim**, que precisa do binario `claude` e de
  credencial. Ele cobre autoteste, entradas podres, shell e instalador. O
  fim-a-fim roda localmente com `./reproduzir.sh`.
- **Portabilidade medida em 3.9, 3.11 e 3.13.** Abaixo de 3.9 nao foi medido.

## Verificacao

```sh
./instalar.sh --check      # pre-requisitos + os dois autotestes
./reproduzir.sh            # autotestes + fim-a-fim (precisa de `claude`)
python3 hooks/agent-route.py --check
python3 bin/emitir-uso.py --check
```

---

## Agent Index da Plow

O `agent-index-client` le um store Hermes com a tabela `session_model_usage`.
**Claude Code nao escreve essa tabela** -- guarda o uso em JSONL sob
`$CLAUDE_CONFIG_DIR/projects/`. `bin/emitir-uso.py` faz a ponte.

```sh
plow-uso --dry-run                      # mede, nao grava
plow-uso --db ~/.hermes/state.db
python3 bin/preflight.py                # confere store + token ANTES de registrar
```

Nunca faz `DROP` nem `DELETE`: o alvo pode ser um store Hermes real com dados de
outra origem. Ha teste provando que a linha alheia sobrevive a uma regravacao.

Passo a passo completo do registro em `INTEGRACAO.md`.

---

## Licenca

MIT -- ver `LICENSE`.
