#!/usr/bin/env python3
"""PreToolUse no tool Agent: injeta contexto de roteamento DENTRO do subagente.

Medido em 2026-09-17 (Claude Code 2.1.274), mesma sonda, so mudando o mecanismo:

    additionalContext -> despachante PRESENTE, subagente AUSENTE
    updatedInput      -> despachante PRESENTE, subagente PRESENTE

Por isso este hook reescreve tool_input["prompt"] em vez de emitir
additionalContext: e a unica das duas portas que atravessa para o subagente.

NUNCA levanta. Hook que quebra BLOQUEIA a chamada da ferramenta -- observado:
um hook com shebang errado barrou o Agent duas vezes seguidas. Silencio e
passagem sao o comportamento correto em qualquer erro.
"""
import json, sys

MARCA = "[camada] contexto roteado para o subagente"


def rotear(prompt: str) -> str:
    """Devolve o cabecalho a prepender. Vazio = nao mexe no prompt.

    ponytail: roteador de verdade entra aqui; hoje so prova a porta.
    """
    return MARCA if prompt.strip() else ""


def transformar(data: dict) -> dict | None:
    """(tool_input reescrito) ou None quando nao ha nada a fazer."""
    ti = data.get("tool_input")
    if not isinstance(ti, dict):
        return None
    prompt = ti.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        return None
    cabeca = rotear(prompt)
    if not cabeca or prompt.startswith(cabeca):   # idempotente: nao empilha
        return None
    novo = dict(ti)
    novo["prompt"] = f"{cabeca}\n\n{prompt}"
    return novo


def main() -> int:
    try:
        data = json.load(sys.stdin)
        novo = transformar(data)
        if novo is not None:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "updatedInput": novo,
            }}, ensure_ascii=False))
    except Exception:
        pass          # jamais bloquear a ferramenta
    return 0


def check() -> int:
    ok = True

    def diz(nome, cond):
        nonlocal ok
        ok = ok and cond
        print(("  ok    " if cond else "  FALHA ") + nome)

    r = transformar({"tool_input": {"prompt": "X", "subagent_type": "general-purpose"}})
    diz("prepende a marca", r is not None and r["prompt"].startswith(MARCA))
    diz("preserva os outros campos", r is not None and r["subagent_type"] == "general-purpose")
    diz("mantem o prompt original", r is not None and r["prompt"].endswith("X"))

    diz("idempotente: nao empilha", transformar({"tool_input": {"prompt": r["prompt"]}}) is None)
    diz("sem prompt -> None", transformar({"tool_input": {"command": "ls"}}) is None)
    diz("prompt vazio -> None", transformar({"tool_input": {"prompt": ""}}) is None)
    diz("tool_input ausente -> None", transformar({}) is None)
    diz("tool_input nao-dict -> None", transformar({"tool_input": "x"}) is None)

    print("PASSOU" if ok else "FALHOU")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(check() if "--check" in sys.argv else main())
