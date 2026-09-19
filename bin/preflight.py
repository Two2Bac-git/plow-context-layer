#!/usr/bin/env python3
"""Confere tudo o que o registro no Agent Index precisa, ANTES de tentar.

Nasceu de uma falha real: o bloco de comandos do README foi colado inteiro,
incluindo a linha `export PLOW_AGENT_TOKEN=...` com o placeholder literal. O
cliente da Plow mandou "..." como credencial e o servidor devolveu 401 -- que
nao diz "seu token e um placeholder", diz apenas "could not get Plow
assertion: 401". Este preflight diz.

Nao imprime o token, nunca. Só comprimento e veredito.
Python 3.9+, stdlib apenas.
"""
import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request

PLOW_API = os.environ.get("PLOW_API_BASE") or "https://api.plow.co"
ENDPOINT = "/v1/auth/index-identity"

# Valores que parecem token mas sao lixo de copia-e-cola. O cliente da Plow
# aceita qualquer string e deixa o servidor recusar; aqui a recusa vem antes.
PLACEHOLDERS = {"...", "…", "<token>", "TOKEN", "SEU_TOKEN", "xxx", "seu-token"}


def checar_store(db):
    """(ok, mensagem)"""
    if not os.path.exists(db):
        return False, "store nao existe: %s\n      rode: plow-uso --db %s" % (db, db)
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    except sqlite3.Error as e:
        return False, "store ilegivel: %s" % e
    try:
        t = con.execute("SELECT 1 FROM sqlite_master "
                        "WHERE name='session_model_usage'").fetchone()
        if not t:
            return False, "store sem a tabela session_model_usage: %s" % db
        n = con.execute("SELECT COUNT(*) FROM session_model_usage").fetchone()[0]
    except sqlite3.Error as e:
        return False, "store corrompido: %s" % e
    finally:
        con.close()
    if n == 0:
        return False, "store vazio (0 linhas): o Indice nao teria o que publicar"
    return True, "store ok: %d linhas em %s" % (n, db)


def classificar_token(tok):
    """(estado, mensagem) sem revelar o valor. Nao toca na rede."""
    if tok is None:
        return "ausente", ("PLOW_AGENT_TOKEN nao esta no ambiente. Para obter:\n"
                           "        plow-agents login\n"
                           "        export PLOW_AGENT_TOKEN=$(cat ~/.config/plow/token)\n"
                           "      Dentro de um container da Plow ele ja vem pronto.")
    if not tok.strip():
        return "vazio", "PLOW_AGENT_TOKEN esta definida mas vazia."
    if tok.strip() in PLACEHOLDERS or tok.strip().strip("<>") in PLACEHOLDERS:
        return "placeholder", ("PLOW_AGENT_TOKEN contem um PLACEHOLDER, nao um token.\n"
                               "      Foi o bloco do README colado inteiro. Substitua o valor.")
    if len(tok.strip()) < 16:
        return "curto", ("PLOW_AGENT_TOKEN tem so %d caracteres -- curto demais "
                         "para um token." % len(tok.strip()))
    return "plausivel", "token presente (%d chars), formato plausivel" % len(tok.strip())


def validar_online(tok, timeout=10):
    """(codigo_http_ou_0, mensagem). Unica funcao que toca a rede."""
    req = urllib.request.Request(
        PLOW_API + ENDPOINT,
        headers={"authorization": "Bearer " + tok, "accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            corpo = json.loads(r.read() or b"{}")
            if isinstance(corpo.get("assertion"), str):
                return r.status, "token ACEITO pela Plow"
            return r.status, "servidor respondeu %s mas sem 'assertion'" % r.status
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return 401, ("token RECUSADO (401). Ele existe mas nao vale para este\n"
                         "      agente, ou expirou. Peca um novo a Plow.")
        return e.code, "servidor recusou com HTTP %d" % e.code
    except Exception as e:
        return 0, "nao deu para falar com %s: %s" % (PLOW_API, type(e).__name__)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=os.path.join(
        os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")), "state.db"))
    ap.add_argument("--offline", action="store_true",
                    help="nao toca na rede; so checa store e formato do token")
    a = ap.parse_args(argv)

    falhou = 0

    av = next((c for c in (os.path.expanduser("~/.local/bin/agentsview"),
                           "/opt/homebrew/bin/agentsview",
                           "/usr/local/bin/agentsview") if os.path.exists(c)), None)
    if av:
        print("  FALHA agentsview instalado em %s -- ele ja reporta Claude Code,\n"
              "        e o merge() do cliente SOMA fontes coincidentes (2.0x medido).\n"
              "        Nao alimente o store Hermes com as mesmas sessoes." % av)
        falhou = 1
    else:
        print("  ok    agentsview ausente: o store Hermes e a unica fonte, sem "
              "risco de contagem dupla")

    ok, msg = checar_store(a.db)
    print(("  ok    " if ok else "  FALHA ") + msg)
    falhou |= (not ok)

    estado, msg = classificar_token(os.environ.get("PLOW_AGENT_TOKEN"))
    print(("  ok    " if estado == "plausivel" else "  FALHA ") + msg)

    if estado != "plausivel":
        falhou = 1
    elif not a.offline:
        codigo, msg = validar_online(os.environ["PLOW_AGENT_TOKEN"])
        print(("  ok    " if codigo == 200 else "  FALHA ") + msg)
        falhou |= (codigo != 200)

    print()
    if falhou:
        print("NAO registre ainda: conserte o que esta em FALHA acima.")
    else:
        print("Tudo pronto. Pode rodar o --register.")
    return 1 if falhou else 0


def check():
    import tempfile
    ok = True

    def diz(nome, cond):
        nonlocal ok
        ok = ok and cond
        print(("  ok    " if cond else "  FALHA ") + nome)

    diz("token ausente e detectado", classificar_token(None)[0] == "ausente")
    diz("token vazio e detectado", classificar_token("   ")[0] == "vazio")
    diz("placeholder '...' e detectado", classificar_token("...")[0] == "placeholder")
    diz("placeholder '<token>' e detectado", classificar_token("<token>")[0] == "placeholder")
    diz("token curto e detectado", classificar_token("abc123")[0] == "curto")
    diz("token plausivel passa", classificar_token("a" * 40)[0] == "plausivel")
    diz("mensagem nunca contem o token",
        "a" * 40 not in classificar_token("a" * 40)[1])

    with tempfile.TemporaryDirectory() as td:
        ausente = os.path.join(td, "nao-existe.db")
        diz("store ausente e detectado", checar_store(ausente)[0] is False)

        vazio = os.path.join(td, "vazio.db")
        con = sqlite3.connect(vazio); con.close()
        diz("store sem tabela e detectado", checar_store(vazio)[0] is False)

        cheio = os.path.join(td, "cheio.db")
        con = sqlite3.connect(cheio)
        con.execute("CREATE TABLE session_model_usage (session_id TEXT, model TEXT)")
        diz("store com tabela mas 0 linhas e detectado", checar_store(cheio)[0] is False)
        con.execute("INSERT INTO session_model_usage VALUES ('s','m')")
        con.commit(); con.close()
        okk, msg = checar_store(cheio)
        diz("store com linha passa", okk)
        diz("mensagem diz quantas linhas", "1 linhas" in msg)

    print("PASSOU" if ok else "FALHOU")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(check() if "--check" in sys.argv else main(sys.argv[1:]))
