#!/usr/bin/env python3
"""PreToolUse no tool Agent: injeta contexto de roteamento DENTRO do subagente.

Medido em 2026-09-17 (Claude Code 2.1.274), mesma sonda, so mudando o mecanismo:

    additionalContext -> despachante PRESENTE, subagente AUSENTE
    updatedInput      -> despachante PRESENTE, subagente PRESENTE

Por isso este hook reescreve tool_input["prompt"] em vez de emitir
additionalContext: e a unica das duas portas que atravessa para o subagente.

NUNCA levanta. Hook que quebra BLOQUEIA a chamada da ferramenta -- observado:
um hook com shebang errado barrou o Agent duas vezes seguidas.

Sem anotacao de tipo com `|`: PEP 604 e 3.10+, e falha de sintaxe acontece no
carregamento do modulo, ANTES do try/except do main(), que e justamente o caso
que a promessa acima nao cobriria. Este arquivo roda em Python 3.6+.
"""
import json
import sys

ALVO = {"Agent", "Task"}          # mesmo padrao do versionado.py do harness
TETO_PALAVRAS = 60                # orcamento do contrato; check() falha se estourar
MARCA_ID = "[camada:rota]"        # sentinela estavel: idempotencia casa por ela,

# O contrato de comportamento -- a unica coisa que esta camada distribui.
# Nasce com UMA clausula. O ruleset de quem escreveu a camada nao vem junto:
# o que viaja e o mecanismo que obriga, nao as regras de ninguem.
#
# Curto de proposito: isto entra no prompt de TODO subagente, entao cada
# palavra aqui e paga N vezes por sessao. O teto e testado em check(), nao
# anotado num comentario -- comentario com numero apodrece em silencio.
CONTRATO = (
    "Contrato desta sessao -- pare antes de destruir. "
    "Antes de executar qualquer acao que apague, sobrescreva ou mova dados "
    "(rm, mv, truncate, DROP, git reset --hard, push --force), NAO execute: "
    "diga o que seria perdido, se e recuperavel, e devolva o comando pronto "
    "para quem te chamou executar. Vale tambem para os subagentes que voce despachar."
)


def rotear(prompt):
    """Corpo do cabecalho a prepender. Vazio = nao mexe.

    ponytail: uma clausula. Roteamento por conteudo do prompt entra aqui
    quando houver mais de uma e valer a pena escolher entre elas.
    """
    return CONTRATO if prompt.strip() else ""


def transformar(data):
    """tool_input reescrito, ou None quando nao ha nada a fazer."""
    if data.get("tool_name") not in ALVO:
        return None
    ti = data.get("tool_input")
    if not isinstance(ti, dict):
        return None
    prompt = ti.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        return None
    if prompt.lstrip().startswith(MARCA_ID):   # ja roteado: nao empilha
        return None
    corpo = rotear(prompt)
    if not corpo:
        return None
    novo = dict(ti)
    novo["prompt"] = "%s %s\n\n%s" % (MARCA_ID, corpo, prompt)
    return novo


def main():
    try:
        novo = transformar(json.load(sys.stdin))
        if novo is not None:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "updatedInput": novo,
            }}, ensure_ascii=False))
    except Exception:
        pass          # jamais bloquear a ferramenta
    return 0


def check():
    ok = True

    def diz(nome, cond):
        nonlocal ok
        ok = ok and cond
        print(("  ok    " if cond else "  FALHA ") + nome)

    def entrada(**ti):
        return {"tool_name": "Agent", "tool_input": ti}

    r = transformar(entrada(prompt="X", subagent_type="general-purpose"))
    diz("prepende a sentinela", r is not None and r["prompt"].startswith(MARCA_ID))
    diz("preserva outros campos", r is not None and r["subagent_type"] == "general-purpose")
    diz("mantem o prompt original", r is not None and r["prompt"].endswith("X"))

    diz("idempotente: nao empilha", transformar(entrada(prompt=r["prompt"])) is None)
    diz("idempotente mesmo com corpo diferente",
        transformar(entrada(prompt=MARCA_ID + " outro corpo\n\nX")) is None)
    diz("idempotente com espaco antes",
        transformar(entrada(prompt="  " + MARCA_ID + " x")) is None)

    diz("tool_name errado -> None",
        transformar({"tool_name": "Bash", "tool_input": {"prompt": "X"}}) is None)
    diz("tool_name ausente -> None", transformar({"tool_input": {"prompt": "X"}}) is None)
    diz("Task tambem passa",
        transformar({"tool_name": "Task", "tool_input": {"prompt": "X"}}) is not None)

    diz("sem prompt -> None", transformar(entrada(command="ls")) is None)
    diz("prompt vazio -> None", transformar(entrada(prompt="")) is None)
    diz("prompt so-espaco -> None", transformar(entrada(prompt="   ")) is None)
    diz("prompt nao-str -> None", transformar(entrada(prompt=123)) is None)
    diz("tool_input ausente -> None", transformar({"tool_name": "Agent"}) is None)
    diz("tool_input nao-dict -> None",
        transformar({"tool_name": "Agent", "tool_input": "x"}) is None)

    n = len(CONTRATO.split())
    diz("contrato cabe no orcamento (%d/%d palavras)" % (n, TETO_PALAVRAS),
        n <= TETO_PALAVRAS)
    diz("contrato nomeia a acao que barra", "pare antes de destruir" in CONTRATO.lower())
    diz("contrato se propaga para netos", "subagentes que voce despachar" in CONTRATO)

    # o contrato que a promessa do docstring depende: entrada podre nao levanta
    try:
        transformar({"tool_name": "Agent", "tool_input": {"prompt": None}})
        diz("entrada podre nao levanta", True)
    except Exception:
        diz("entrada podre nao levanta", False)

    print("PASSOU" if ok else "FALHOU")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(check() if "--check" in sys.argv else main())
