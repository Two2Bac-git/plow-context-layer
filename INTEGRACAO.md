# Integrar com o Agent Index da Plow

O `agent-index-client` coleta de dois lugares. O help deles diz, textualmente,
que via agentsview **"hermes reports zero with no fix known"** -- por isso ele
tambem le o store Hermes direto. `bin/emitir-uso.py` e o que enche esse store
quando o agente roda em Claude Code, que nao escreve `session_model_usage`.

## Sequencia

```sh
# 1. cliente da Plow (Python stdlib, sem dependencia)
curl -O https://raw.githubusercontent.com/plow-pbc/agent-index-client/main/standalone/agent_index_client.py

# 2. encher o store que ele le
python3 bin/emitir-uso.py --dry-run                     # confere antes
python3 bin/emitir-uso.py --db ~/.hermes/state.db

# 3. credencial (nunca commitar, nunca assar em imagem)
export PLOW_AGENT_TOKEN=...

# 4. registrar a pagina publica do agente
python3 agent_index_client.py --register --agent plow-context-layer \
  --name  "plow-context-layer" \
  --blurb "Injeta um contrato de comportamento no prompt de todo subagente. Medido: com a camada o subagente recusa destruir; sem ela, destroi." \
  --repo        https://github.com/Two2Bac-git/plow-context-layer \
  --install-url https://github.com/Two2Bac-git/plow-context-layer

# 5. reportar uso
python3 agent_index_client.py --agent plow-context-layer --dry-run
python3 agent_index_client.py --agent plow-context-layer
python3 agent_index_client.py status
```

## A PRIMEIRA execucao reporta ZERO -- isso e normal

Medido com a funcao deles (`from_hermes`) sobre um store nosso:

```
1a chamada: "Hermes baseline recorded - usage is reported from the next run on."
            devolveu {}
2a chamada: {"2026-09-18": {"claude-opus-5": {"input": 78, "output": 44529,
             "cache_read": 13140181, "cache_write": 1129730}}}
```

O cliente reporta **delta** entre snapshots, nao total. A primeira vez so grava
a linha de base. Rode o emissor e o report **duas vezes**, com uso real no meio,
ou a pagina do agente nasce zerada e parece quebrada.

```sh
python3 bin/emitir-uso.py --db ~/.hermes/state.db
python3 agent_index_client.py --agent plow-context-layer      # grava baseline, reporta 0
#   ... use o agente normalmente ...
python3 bin/emitir-uso.py --db ~/.hermes/state.db
python3 agent_index_client.py --agent plow-context-layer      # agora sai numero
```

## Onde o cliente escreve

`$HERMES_HOME/.agent-index/` ou `~/.agent-index/` -- `token` e
`hermes-state.json`. Um diretorio, com nome proprio. E so isso.

O docstring de `state_dir()` deles explica por que a identidade do install mora
no volume e nao no HOME: container recriado tem HOME novo e mintaria um segundo
id, orfanando o que o primeiro escreveu. A imagem oficial da Plow resolve com
`HERMES_HOME=/opt/data` num volume que o container guarda.

## Gotcha observado

`--self-check` sem token estoura com stack trace em vez de mensagem: o
`_post()` deles faz `json.loads(e.read() or b"{}")` e quebra quando a resposta
de erro nao e JSON. Nao e problema desta camada, mas confunde no primeiro uso.
