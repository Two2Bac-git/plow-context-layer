#!/usr/bin/env python3
"""Emite session_model_usage a partir dos transcripts do Claude Code.

O agent-index-client da Plow le um store Hermes: SQLite com a tabela
session_model_usage. Claude Code NAO escreve essa tabela -- ele guarda o uso
em JSONL sob $CLAUDE_CONFIG_DIR/projects/. Esta ponte le um e alimenta o outro,
para que um agente rodando em Claude Code tenha numero para publicar no Indice.

Colunas exigidas pelo cliente (medido em plow-pbc/agent-index-client, 2026-09-17):
    model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens
Mais session_id, que ele usa como parte da chave quando existe.

Mapeamento medido nos transcripts:
    usage.input_tokens                -> input_tokens
    usage.output_tokens               -> output_tokens
    usage.cache_read_input_tokens     -> cache_read_tokens
    usage.cache_creation_input_tokens -> cache_write_tokens

NUNCA faz DROP nem DELETE: o alvo pode ser um store Hermes de verdade, com
dados de outra origem. Cria a tabela se faltar e regrava so as proprias chaves.
Le os transcripts em modo leitura; nao escreve neles.

Python 3.6+, stdlib apenas -- mesma disciplina do cliente da Plow, que roda
"where the agent runs, which is usually a container or a small VPS".
"""
import argparse
import json
import os
import sqlite3
import sys

COLUNAS = ("session_id", "model", "input_tokens", "output_tokens",
           "cache_read_tokens", "cache_write_tokens")
CAMPOS = (("input_tokens", "input_tokens"),
          ("output_tokens", "output_tokens"),
          ("cache_read_input_tokens", "cache_read_tokens"),
          ("cache_creation_input_tokens", "cache_write_tokens"))
IGNORAR = {"<synthetic>"}          # nao e modelo; aparece nos transcripts

# Os MESMOS tres caminhos que o agent_index_client procura (from_agentsview).
# Se o agentsview existe, ELE ja reporta o uso de Claude Code -- e o merge()
# do cliente SOMA fontes que coincidem, em vez de uma vencer. Medido com a
# funcao deles: 100 -> 200, fator 2.0x. Publicar dobrado num placar de
# contagem honesta e pior do que nao publicar.
AGENTSVIEW_PATHS = (
    os.path.expanduser("~/.local/bin/agentsview"),
    "/opt/homebrew/bin/agentsview",
    "/usr/local/bin/agentsview",
)


def agentsview_instalado(caminhos=None):
    """Caminho do agentsview, ou None. Mesma busca que o cliente da Plow faz.

    `caminhos=None` e resolvido na CHAMADA, nao na definicao: default mutavel
    ligado no `def` nao enxerga quem troca a constante depois, e foi assim que
    o primeiro teste desta guarda passou verde sem exercitar nada.
    """
    return next((c for c in (caminhos or AGENTSVIEW_PATHS) if os.path.exists(c)), None)


def linhas_de_uso(caminho):
    """(session_id, model, {coluna: int}) por registro com usage."""
    sid_arquivo = None
    try:
        fh = open(caminho, encoding="utf-8", errors="replace")
    except OSError:
        return
    with fh:
        for linha in fh:
            try:
                d = json.loads(linha)
            except Exception:
                continue
            sid_arquivo = d.get("sessionId") or sid_arquivo
            msg = d.get("message") or {}
            uso = msg.get("usage") or d.get("usage")
            if not isinstance(uso, dict):
                continue
            modelo = msg.get("model")
            if not modelo or modelo in IGNORAR:
                continue
            vals = {}
            for origem, destino in CAMPOS:
                v = uso.get(origem)
                vals[destino] = v if isinstance(v, int) and v >= 0 else 0
            yield (sid_arquivo or os.path.basename(caminho), modelo, vals)


def agregar(caminhos):
    """{(session_id, model): {coluna: total}}"""
    fora = {}
    for c in caminhos:
        for sid, modelo, vals in linhas_de_uso(c):
            alvo = fora.setdefault((sid, modelo),
                                   {d: 0 for _, d in CAMPOS})
            for k, v in vals.items():
                alvo[k] += v
    return fora


def transcripts(raiz):
    for base, _dirs, arqs in os.walk(raiz):
        for a in arqs:
            if a.endswith(".jsonl"):
                yield os.path.join(base, a)


def gravar(db, agregado):
    """Cria a tabela se faltar e regrava so as chaves proprias. Devolve n linhas."""
    con = sqlite3.connect(db)
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS session_model_usage ("
            "session_id TEXT NOT NULL, model TEXT NOT NULL,"
            " input_tokens INTEGER NOT NULL DEFAULT 0,"
            " output_tokens INTEGER NOT NULL DEFAULT 0,"
            " cache_read_tokens INTEGER NOT NULL DEFAULT 0,"
            " cache_write_tokens INTEGER NOT NULL DEFAULT 0,"
            " PRIMARY KEY (session_id, model))")
        con.executemany(
            "INSERT OR REPLACE INTO session_model_usage (%s) VALUES (?,?,?,?,?,?)"
            % ",".join(COLUNAS),
            [(sid, mod, v["input_tokens"], v["output_tokens"],
              v["cache_read_tokens"], v["cache_write_tokens"])
             for (sid, mod), v in sorted(agregado.items())])
        con.commit()
    finally:
        con.close()
    return len(agregado)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--projects", default=os.path.join(
        os.environ.get("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude")), "projects"),
        help="raiz dos transcripts do Claude Code")
    ap.add_argument("--db", default=os.path.join(
        os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")), "state.db"),
        help="store Hermes que o agent-index-client vai ler")
    ap.add_argument("--dry-run", action="store_true",
                    help="mede e mostra, sem gravar nada")
    ap.add_argument("--mesmo-com-agentsview", action="store_true",
                    help="grava mesmo se o agentsview existir (aceita o risco "
                         "de contagem dupla; so faz sentido se o agentsview "
                         "nao cobrir estas sessoes)")
    a = ap.parse_args(argv)

    av = agentsview_instalado()
    if av and not a.dry_run and not a.mesmo_com_agentsview:
        print("RECUSADO: agentsview instalado em %s\n"
              "  Ele ja reporta o uso de Claude Code, e o merge() do cliente da\n"
              "  Plow SOMA fontes coincidentes em vez de escolher uma: o mesmo\n"
              "  (dia, modelo) sairia com o dobro. Medido, fator 2.0x.\n"
              "  Se ainda assim quiser gravar: --mesmo-com-agentsview" % av,
              file=sys.stderr)
        return 2

    if not os.path.isdir(a.projects):
        print("nada a fazer: %s nao existe" % a.projects, file=sys.stderr)
        return 1
    ag = agregar(transcripts(a.projects))
    tot = sum(v["input_tokens"] + v["output_tokens"] for v in ag.values())
    print("sessoes x modelo : %d" % len(ag))
    print("tokens in+out    : %d" % tot)
    if a.dry_run:
        print("(dry-run: nada gravado)")
        return 0
    d = os.path.dirname(a.db)
    if d:
        os.makedirs(d, exist_ok=True)
    print("gravadas %d linhas em %s" % (gravar(a.db, ag), a.db))
    return 0


def check():
    import tempfile
    ok = True

    def diz(nome, cond):
        nonlocal ok
        ok = ok and cond
        print(("  ok    " if cond else "  FALHA ") + nome)

    with tempfile.TemporaryDirectory() as td:
        t = os.path.join(td, "s.jsonl")
        with open(t, "w", encoding="utf-8") as f:
            for u in ({"input_tokens": 10, "output_tokens": 5,
                       "cache_read_input_tokens": 3, "cache_creation_input_tokens": 7},
                      {"input_tokens": 1, "output_tokens": 2,
                       "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}):
                f.write(json.dumps({"sessionId": "S1",
                                    "message": {"model": "m1", "usage": u}}) + "\n")
            f.write(json.dumps({"sessionId": "S1",
                                "message": {"model": "<synthetic>",
                                            "usage": {"input_tokens": 999}}}) + "\n")
            f.write(json.dumps({"sessionId": "S1", "message": {"model": "m1"}}) + "\n")
            f.write("{lixo nao-json\n")

        ag = agregar([t])
        diz("agrega por (sessao, modelo)", list(ag) == [("S1", "m1")])
        v = ag[("S1", "m1")]
        diz("soma input  (10+1=11)", v["input_tokens"] == 11)
        diz("soma output (5+2=7)", v["output_tokens"] == 7)
        diz("mapeia cache_read_input -> cache_read", v["cache_read_tokens"] == 3)
        diz("mapeia cache_creation -> cache_write", v["cache_write_tokens"] == 7)
        diz("ignora <synthetic>", all(m != "<synthetic>" for _, m in ag))
        diz("linha sem usage nao quebra nem conta", v["input_tokens"] == 11)
        diz("linha nao-json nao quebra", True)

        db = os.path.join(td, "state.db")
        diz("grava o numero de linhas que agregou", gravar(db, ag) == 1)
        con = sqlite3.connect(db)
        linha = con.execute("SELECT %s FROM session_model_usage" % ",".join(COLUNAS)).fetchone()
        diz("le de volta o que gravou", linha == ("S1", "m1", 11, 7, 3, 7))

        cols = {r[1] for r in con.execute("PRAGMA table_info(session_model_usage)")}
        diz("tabela tem as colunas que o cliente exige",
            {"model", "input_tokens", "output_tokens",
             "cache_read_tokens", "cache_write_tokens"} <= cols)

        # idempotencia: rodar duas vezes nao duplica nem dobra
        gravar(db, ag)
        n = con.execute("SELECT COUNT(*) FROM session_model_usage").fetchone()[0]
        diz("idempotente: regravar nao duplica", n == 1)
        soma = con.execute("SELECT input_tokens FROM session_model_usage").fetchone()[0]
        diz("idempotente: regravar nao dobra o total", soma == 11)

        # nao destroi dado de outra origem
        con.execute("INSERT INTO session_model_usage (session_id, model) VALUES ('OUTRO','x')")
        con.commit()
        gravar(db, ag)
        sobrevive = con.execute(
            "SELECT COUNT(*) FROM session_model_usage WHERE session_id='OUTRO'").fetchone()[0]
        diz("preserva linha de outra origem", sobrevive == 1)
        con.close()

        diz("projects inexistente devolve 1, nao levanta",
            main(["--projects", os.path.join(td, "nao-existe")]) == 1)

        # guarda de contagem dupla
        falso = os.path.join(td, "agentsview")
        diz("agentsview ausente e detectado como None",
            agentsview_instalado((falso,)) is None)
        open(falso, "w").close()
        diz("agentsview presente e detectado",
            agentsview_instalado((falso,)) == falso)
        diz("detector usa os 3 caminhos do cliente da Plow",
            len(AGENTSVIEW_PATHS) == 3
            and any(c.endswith("/.local/bin/agentsview") for c in AGENTSVIEW_PATHS))

    print("PASSOU" if ok else "FALHOU")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(check() if "--check" in sys.argv else main(sys.argv[1:]))
