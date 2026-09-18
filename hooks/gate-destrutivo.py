#!/usr/bin/env python3
"""PreToolUse em Bash: BLOQUEIA comando destrutivo, em vez de pedir por favor.

O contrato injetado por agent-route.py persuade o subagente. Este hook impede.
Medido: hook em evento de ferramenta ALCANCA o subagente; hook em
UserPromptSubmit nao. Por isso o bloqueio mora aqui e nao la.

Casa por CABECA de comando, nunca por substring. Isso nao e preciosismo:
`"find" in cmd` ja casou com a palavra `find` dentro de um heredoc Python e
acusou um comando que nao tinha find nenhum. Aqui o corpo de heredoc e
removido antes de qualquer analise, e so o primeiro token de cada segmento
conta como comando.

Truncamento por `>` so e bloqueado quando o alvo JA EXISTE -- criar arquivo
novo nao destroi nada. Mesmo criterio do hook de versionamento: silencio no
que e novo.

NUNCA levanta: hook que quebra bloqueia TUDO, inclusive o que era inofensivo.
Em qualquer erro, sai em silencio e deixa passar -- falhar aberto e a escolha
deliberada, porque um gate que derruba a sessao e pior que um gate ausente.

Python 3.9+, stdlib apenas.
"""
import json
import os
import re
import shlex
import sys

# Cabecas de comando que destroem dado por natureza.
CABECAS = {
    "rm": "apaga arquivos",
    "shred": "sobrescreve e apaga, sem recuperacao",
    "truncate": "zera o conteudo do arquivo",
    "dd": "escreve direto no dispositivo ou arquivo",
    "mkfs": "formata sistema de arquivos",
}
# Prefixos que nao sao o comando de verdade; o comando vem depois.
TRANSPARENTES = {"sudo", "doas", "command", "env", "nohup", "time", "xargs"}

# git e destrutivo so em certas formas
GIT_DESTRUTIVO = (
    (("reset",), ("--hard",), "descarta alteracoes nao commitadas"),
    (("clean",), ("-f", "-fd", "-fdx", "-xdf", "-df"), "apaga arquivos nao rastreados"),
    (("push",), ("--force", "-f"), "reescreve historico remoto"),
    (("branch",), ("-D",), "apaga branch sem checar merge"),
    (("checkout", "restore"), ("--force",), "descarta alteracoes locais"),
)

REDIR = re.compile(r"(?<![0-9>&])>(?!>)\s*([^\s;|&<>]+)")


def sem_heredoc(cmd):
    """Remove o CORPO de cada heredoc. O corpo e texto, nao comando."""
    linhas = cmd.split("\n")
    fora, i = [], 0
    while i < len(linhas):
        fora.append(linhas[i])
        m = re.search(r"<<-?\s*[\"']?([A-Za-z_][A-Za-z0-9_]*)[\"']?", linhas[i])
        if m:
            delim = m.group(1)
            i += 1
            while i < len(linhas) and linhas[i].strip() != delim:
                i += 1        # corpo descartado
            if i < len(linhas):
                fora.append(linhas[i])   # a linha do delimitador fica
        i += 1
    return "\n".join(fora)


def segmentos(cmd):
    """Quebra em segmentos por separador de comando."""
    return [s for s in re.split(r"(?:\|\||&&|[;\n|&()])", cmd) if s.strip()]


def cabeca(seg):
    """Primeiro token real do segmento, ou None."""
    try:
        toks = shlex.split(seg, comments=True)
    except ValueError:
        toks = seg.split()          # aspas nao fechadas: degrada, nao quebra
    for t in toks:
        if "=" in t and not t.startswith("-") and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):
            continue                # VAR=valor antes do comando
        base = os.path.basename(t)
        if base in TRANSPARENTES:
            continue
        return base, toks
    return None, []


def motivos(cmd, existe=os.path.exists):
    """Lista de motivos de bloqueio. Vazia = pode passar."""
    achados = []
    limpo = sem_heredoc(cmd)
    for seg in segmentos(limpo):
        base, toks = cabeca(seg)
        if not base:
            continue
        raiz = base.split(".")[0]
        if base in CABECAS or raiz in CABECAS:
            achados.append("`%s` %s" % (base, CABECAS.get(base) or CABECAS[raiz]))
            continue
        if base == "git" and len(toks) > 1:
            sub = next((t for t in toks[1:] if not t.startswith("-")), "")
            flags = {t for t in toks if t.startswith("-")}
            for subs, precisa, porque in GIT_DESTRUTIVO:
                if sub in subs and flags & set(precisa):
                    achados.append("`git %s` %s" % (sub, porque))
        for alvo in REDIR.findall(seg):
            if existe(os.path.expanduser(alvo)):
                achados.append("`> %s` truncaria um arquivo que JA EXISTE" % alvo)
    return achados


def decidir(data, existe=os.path.exists):
    """Texto do motivo, ou None para deixar passar."""
    if data.get("tool_name") != "Bash":
        return None
    cmd = (data.get("tool_input") or {}).get("command")
    if not isinstance(cmd, str) or not cmd.strip():
        return None
    ms = motivos(cmd, existe)
    if not ms:
        return None
    return ("Bloqueado pelo contrato desta sessao: pare antes de destruir.\n"
            + "\n".join("  - " + m for m in ms)
            + "\n\nDiga o que seria perdido e se e recuperavel, e devolva o "
              "comando pronto para quem te chamou executar. Nao contorne este "
              "gate nem o desative.")


def main():
    try:
        motivo = decidir(json.load(sys.stdin))
        if motivo:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": motivo,
            }}, ensure_ascii=False))
    except Exception:
        pass        # falha ABERTA: gate que derruba a sessao e pior que gate ausente
    return 0


def check():
    ok = True

    def diz(nome, cond):
        nonlocal ok
        ok = ok and cond
        print(("  ok    " if cond else "  FALHA ") + nome)

    def bloqueia(cmd, existe=lambda p: False):
        return bool(motivos(cmd, existe))

    # --- destroi: tem de bloquear ---
    diz("rm", bloqueia("rm -rf /tmp/x"))
    diz("shred", bloqueia("shred -u arquivo"))
    diz("truncate", bloqueia("truncate -s 0 arquivo"))
    diz("dd", bloqueia("dd if=/dev/zero of=/tmp/x"))
    diz("mkfs.ext4", bloqueia("mkfs.ext4 /dev/sdb1"))
    diz("sudo rm (prefixo transparente)", bloqueia("sudo rm -rf /x"))
    diz("VAR=1 rm (atribuicao antes)", bloqueia("FOO=1 rm -rf /x"))
    diz("rm depois de && ", bloqueia("cd /tmp && rm -rf x"))
    diz("rm depois de ; ", bloqueia("echo oi; rm -rf x"))
    diz("rm no fim de pipe", bloqueia("ls | xargs rm"))
    diz("caminho absoluto /bin/rm", bloqueia("/bin/rm -rf /x"))

    # --- git destrutivo so nas formas certas ---
    diz("git reset --hard", bloqueia("git reset --hard HEAD~1"))
    diz("git reset (sem --hard) passa", not bloqueia("git reset HEAD~1"))
    diz("git clean -fd", bloqueia("git clean -fd"))
    diz("git clean --dry-run passa", not bloqueia("git clean --dry-run"))
    diz("git push --force", bloqueia("git push --force origin main"))
    diz("git push normal passa", not bloqueia("git push origin main"))
    diz("git branch -D", bloqueia("git branch -D velho"))

    # --- ERRO #4 do catalogo: cabeca, nao substring ---
    heredoc = "python3 - <<\'EOF\'\nimport os\nos.system(\"rm -rf /\")\nEOF"
    diz("rm DENTRO de heredoc nao bloqueia (erro #4)", not bloqueia(heredoc))
    diz("rm dentro de string de echo nao bloqueia", not bloqueia('echo "rm -rf /"'))
    diz("grep por 'rm' nao bloqueia", not bloqueia('grep -r "rm" .'))
    diz("palavra rm no meio de nome nao bloqueia", not bloqueia("./rmdir-helper.sh"))
    diz("comando 'format' nao vira mkfs", not bloqueia("format-code --write"))

    # --- truncamento: so o que ja existe ---
    diz("> arquivo NOVO passa", not bloqueia("echo x > /tmp/novo"))
    diz("> arquivo EXISTENTE bloqueia", bloqueia("echo x > /tmp/velho", lambda p: True))
    diz(">> append passa mesmo existindo", not bloqueia("echo x >> /tmp/v", lambda p: True))

    # --- portas fechadas ---
    diz("tool_name != Bash passa", decidir({"tool_name": "Read"}) is None)
    diz("sem comando passa", decidir({"tool_name": "Bash", "tool_input": {}}) is None)
    diz("comando vazio passa", decidir({"tool_name": "Bash",
                                        "tool_input": {"command": "  "}}) is None)
    diz("comando nao-str passa", decidir({"tool_name": "Bash",
                                          "tool_input": {"command": 7}}) is None)
    diz("aspas nao fechadas nao levantam", bloqueia('rm -rf "aberta') in (True, False))

    m = decidir({"tool_name": "Bash", "tool_input": {"command": "rm -rf /x"}})
    diz("motivo cita o contrato", m is not None and "pare antes de destruir" in m)
    diz("motivo proibe contornar", m is not None and "Nao contorne" in m)

    print("PASSOU" if ok else "FALHOU")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(check() if "--check" in sys.argv else main())
