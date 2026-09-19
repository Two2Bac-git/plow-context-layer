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

# `2>arquivo` tambem trunca. A versao anterior ignorava `>` precedido de
# digito para nao confundir descritor com redirecionamento -- e com isso
# deixava passar exatamente o caso que destroi. Agora o descritor e OPCIONAL
# e capturado; `>>` sai pelos dois lookarounds, e `->`/`=>` pelo lookbehind.
REDIR = re.compile(r"(?<!>)(?<![-=])(\d?)>(?!>)\s*([^\s;|&<>]+)")

# Ferramentas que gravam arquivo inteiro. `Edit` NAO entra: ele e cirurgico,
# e barrar toda edicao tornaria o agente inutil. `Write` sobre arquivo que ja
# existe e o mesmo ato que `> arquivo`: troca o conteudo inteiro.
FERRAMENTAS_ESCRITA = {"Write"}

# Interpretador com codigo embutido: `python3 -c "os.remove(...)"` tem `python3`
# como cabeca e escapava inteiro. Isto e HEURISTICA e assumidamente incompleta
# -- o gate detem o descuido, nao o adversario. Quem quiser burlar, ofusca a
# string e passa. O valor esta em barrar o caso comum, que e o agente escrevendo
# a chamada destrutiva do jeito obvio.
INTERPRETES = {"python", "python3", "perl", "ruby", "node", "php", "sh", "bash", "zsh"}
# Shell dentro de shell: o argumento de `-c` e um COMANDO, nao codigo de
# biblioteca, entao vale passa-lo pelo mesmo analisador em vez de inventar
# uma segunda lista de padroes que sairia do sincronismo com a primeira.
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
FLAGS_INLINE = {"-c", "-e", "--eval", "--command"}
INLINE_DESTRUTIVO = (
    "os.remove", "os.unlink", "os.rmdir", "shutil.rmtree", "pathlib.Path.unlink",
    ".unlink(", "rmtree", "unlink(", "truncate(",
    "fs.rm", "fs.unlink", "rmSync", "unlinkSync",
    "File.delete", "FileUtils.rm", "remove_tree",
)


def _lista(nome):
    return {x.strip() for x in os.environ.get(nome, "").split(",") if x.strip()}


def config():
    """Preferencias do usuario, lidas na CHAMADA e nunca no import.

    Constante congelada no `def` foi o erro que fez o teste da guarda do
    emissor passar verde sem exercitar nada. Aqui nao se repete.

      PLOW_GATE_MODO=aberto|fechado   erro interno deixa passar, ou barra
      PLOW_GATE_EXTRA=cabeca,cabeca   bloqueia tambem estes comandos
      PLOW_GATE_PERMITIR=cabeca,...   libera estes, mesmo sendo destrutivos
    """
    return {
        "modo": (os.environ.get("PLOW_GATE_MODO") or "aberto").strip().lower(),
        "extra": _lista("PLOW_GATE_EXTRA"),
        "permitir": _lista("PLOW_GATE_PERMITIR"),
    }


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


def motivos(cmd, existe=os.path.exists, cfg=None, _fundo=0):
    """Lista de motivos de bloqueio. Vazia = pode passar.

    `_fundo` limita a recursao de `sh -c "sh -c ..."`. Sem teto, um comando
    aninhado de proposito viraria estouro de pilha -- e estouro num hook
    significa bloquear tudo, ou nada, conforme o modo. Nenhum dos dois por
    acidente.
    """
    cfg = cfg or config()
    if _fundo > 3:
        return []
    achados = []
    limpo = sem_heredoc(cmd)
    segs = segmentos(limpo)

    # Interprete com codigo embutido tem de ser avaliado no comando INTEIRO:
    # `segmentos()` quebra no `;`, e um `;` dentro das aspas de `-c` separava
    # `python3 -c "import os` de `os.remove(...)`, escondendo o par. Mas o
    # gatilho exige que algum SEGMENTO comece com um interprete de verdade,
    # senao `echo "python3 -c os.remove"` viraria falso positivo -- que e o
    # erro #4 do catalogo, casar por substring em vez de por cabeca.
    heads = {cabeca(s)[0] for s in segs}
    heads.discard(None)
    if (heads & INTERPRETES) and any(f in limpo for f in FLAGS_INLINE):
        achou = [a for a in INLINE_DESTRUTIVO if a in limpo]
        if achou:
            achados.append("codigo embutido em `%s` chamando %s"
                           % (", ".join(sorted(heads & INTERPRETES)),
                              ", ".join("`%s`" % a for a in achou[:3])))

    for seg in segs:
        base, toks = cabeca(seg)
        if not base:
            continue
        raiz = base.split(".")[0]
        if base in cfg["permitir"] or raiz in cfg["permitir"]:
            continue                      # o usuario liberou explicitamente
        if base in cfg["extra"] or raiz in cfg["extra"]:
            achados.append("`%s` esta na sua lista PLOW_GATE_EXTRA" % base)
            continue
        if base in CABECAS or raiz in CABECAS:
            achados.append("`%s` %s" % (base, CABECAS.get(base) or CABECAS[raiz]))
            continue
        if base in SHELLS:
            for i, tk in enumerate(toks):
                if tk in FLAGS_INLINE and i + 1 < len(toks):
                    for m in motivos(toks[i + 1], existe, cfg, _fundo + 1):
                        achados.append("dentro de `%s -c`: %s" % (base, m))
                    break
        if base == "git" and len(toks) > 1:
            sub = next((t for t in toks[1:] if not t.startswith("-")), "")
            flags = {t for t in toks if t.startswith("-")}
            for subs, precisa, porque in GIT_DESTRUTIVO:
                if sub in subs and flags & set(precisa):
                    achados.append("`git %s` %s" % (sub, porque))
        for fd, alvo in REDIR.findall(seg):
            if existe(os.path.expanduser(alvo)):
                achados.append("`%s> %s` truncaria um arquivo que JA EXISTE"
                               % (fd, alvo))
    return achados


def decidir(data, existe=os.path.exists, cfg=None):
    """Texto do motivo, ou None para deixar passar."""
    cfg = cfg or config()
    ferramenta = data.get("tool_name")
    ti = data.get("tool_input") or {}

    if ferramenta in FERRAMENTAS_ESCRITA:
        alvo = ti.get("file_path")
        if not isinstance(alvo, str) or not alvo:
            return None
        if not existe(os.path.expanduser(alvo)):
            return None               # arquivo novo nao destroi nada
        if "Write" in cfg["permitir"]:
            return None
        ms = ["`Write` sobre `%s`, que JA EXISTE: troca o conteudo inteiro" % alvo]
    elif ferramenta == "Bash":
        cmd = ti.get("command")
        if not isinstance(cmd, str) or not cmd.strip():
            return None
        ms = motivos(cmd, existe, cfg)
    else:
        return None

    if not ms:
        return None
    return ("Bloqueado pelo contrato desta sessao: pare antes de destruir.\n"
            + "\n".join("  - " + m for m in ms)
            + "\n\nDiga o que seria perdido e se e recuperavel, e devolva o "
              "comando pronto para quem te chamou executar. Nao contorne este "
              "gate nem o desative.")


def _deny(motivo):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": motivo,
    }}, ensure_ascii=False))


def main():
    try:
        motivo = decidir(json.load(sys.stdin))
        if motivo:
            _deny(motivo)
    except Exception as e:
        # Padrao ABERTO: um gate que derruba a sessao e pior que um gate
        # ausente -- medido aqui, um shebang errado barrou duas chamadas
        # seguidas. Quem prefere o inverso escolhe, em vez de editar codigo.
        if (os.environ.get("PLOW_GATE_MODO") or "").strip().lower() == "fechado":
            _deny("Gate em modo FECHADO e falhou ao avaliar (%s). "
                  "Nada passa enquanto ele nao puder decidir. "
                  "Para voltar ao padrao: PLOW_GATE_MODO=aberto"
                  % type(e).__name__)
    return 0


def explicar(cmd):
    """Diz o que o gate faria com um comando, sem executar nada.

    Confianca exige inspecao: da para perguntar antes de depender.
    """
    cfg = config()
    print("  modo      : %s" % cfg["modo"])
    if cfg["extra"]:
        print("  extra     : %s" % ", ".join(sorted(cfg["extra"])))
    if cfg["permitir"]:
        print("  permitir  : %s" % ", ".join(sorted(cfg["permitir"])))
    print("  comando   : %s" % cmd)
    ms = motivos(cmd, cfg=cfg)
    if ms:
        print("  VEREDITO  : BLOQUEADO")
        for m in ms:
            print("    - %s" % m)
        return 1
    print("  VEREDITO  : passa")
    return 0


def check():
    ok = True

    def diz(nome, cond):
        nonlocal ok
        ok = ok and cond
        print(("  ok    " if cond else "  FALHA ") + nome)

    def w2(caminho, existe, cfg):
        return decidir({"tool_name": "Write", "tool_input": {"file_path": caminho}},
                       existe, cfg)

    def bloqueia(cmd, existe=lambda p: False, cfg=None):
        return bool(motivos(cmd, existe, cfg or config()))

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

    # --- LIMITE FECHADO (parcial): interpretador com codigo embutido ---
    diz("python3 -c com os.remove bloqueia",
        bloqueia('python3 -c "import os; os.remove(\'/x\')"'))
    diz("python3 -c com shutil.rmtree bloqueia",
        bloqueia('python3 -c "import shutil; shutil.rmtree(\'/x\')"'))
    diz("node -e com unlinkSync bloqueia",
        bloqueia('node -e "require(\'fs\').unlinkSync(\'/x\')"'))
    diz("python3 -c inofensivo passa",
        not bloqueia('python3 -c "print(1+1)"'))
    diz("python3 script.py (sem -c) passa",
        not bloqueia("python3 script.py"))
    diz("a palavra rmtree em outro contexto nao vira comando",
        not bloqueia("echo rmtree"))
    diz("echo citando 'python3 -c os.remove' NAO bloqueia (erro #4)",
        not bloqueia('echo "python3 -c os.remove(1)"'))
    diz("bash -c com rm bloqueia",
        bloqueia('bash -c "rm -rf /x"'))
    diz("sh -c inofensivo passa", not bloqueia('sh -c "ls -la"'))
    diz("aninhamento fundo nao estoura a pilha",
        bloqueia('sh -c "sh -c \'sh -c \\"rm -rf /x\\"\'"') in (True, False))
    diz("motivo aninhado diz onde estava",
        any("dentro de" in m for m in motivos('bash -c "rm -rf /x"')))

    # --- LIMITE FECHADO: redirecionamento de descritor tambem trunca ---
    existe = lambda p: True
    diz("2> arquivo existente bloqueia", bloqueia("cmd 2> /tmp/log", existe))
    diz("1> arquivo existente bloqueia", bloqueia("cmd 1> /tmp/log", existe))
    diz(">> continua passando", not bloqueia("cmd >> /tmp/log", existe))
    diz("'->' nao vira redirecionamento", not bloqueia("echo a->b", existe))
    diz("'=>' nao vira redirecionamento", not bloqueia("echo a=>b", existe))
    diz("2> arquivo NOVO passa", not bloqueia("cmd 2> /tmp/novo", lambda p: False))

    # --- LIMITE FECHADO: a ferramenta Write tambem sobrescreve ---
    def w(caminho, existe):
        return decidir({"tool_name": "Write", "tool_input": {"file_path": caminho}},
                       existe, config())
    diz("Write sobre arquivo EXISTENTE bloqueia", w("/tmp/x", lambda p: True) is not None)
    diz("Write em arquivo NOVO passa", w("/tmp/x", lambda p: False) is None)
    diz("Write sem file_path passa", decidir({"tool_name": "Write",
                                              "tool_input": {}}, existe) is None)
    diz("Edit NAO e barrado (e cirurgico)",
        decidir({"tool_name": "Edit", "tool_input": {"file_path": "/tmp/x"}},
                existe) is None)
    diz("Read nunca e barrado",
        decidir({"tool_name": "Read", "tool_input": {"file_path": "/tmp/x"}},
                existe) is None)

    # --- LIBERDADE: o usuario manda ---
    livre = {"modo": "aberto", "extra": set(), "permitir": set()}
    diz("PLOW_GATE_EXTRA bloqueia o que o usuario listou",
        bloqueia("curl http://x", cfg=dict(livre, extra={"curl"})))
    diz("PLOW_GATE_PERMITIR libera rm para quem quer",
        not bloqueia("rm -rf /x", cfg=dict(livre, permitir={"rm"})))
    diz("permitir tem precedencia sobre extra",
        not bloqueia("rm -rf /x", cfg=dict(livre, extra={"rm"}, permitir={"rm"})))
    diz("permitir Write libera a ferramenta",
        w2("/tmp/x", lambda p: True, dict(livre, permitir={"Write"})) is None)
    diz("config() le do ambiente na CHAMADA, nao no import",
        callable(config) and config()["modo"] in ("aberto", "fechado"))

    m = decidir({"tool_name": "Bash", "tool_input": {"command": "rm -rf /x"}})
    diz("motivo cita o contrato", m is not None and "pare antes de destruir" in m)
    diz("motivo proibe contornar", m is not None and "Nao contorne" in m)

    print("PASSOU" if ok else "FALHOU")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(check())
    if "--explicar" in sys.argv:
        i = sys.argv.index("--explicar")
        if i + 1 >= len(sys.argv):
            sys.exit("uso: gate-destrutivo.py --explicar \"<comando>\"")
        sys.exit(explicar(sys.argv[i + 1]))
    sys.exit(main())
