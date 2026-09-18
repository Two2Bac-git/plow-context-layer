#!/bin/sh
# Instala a camada para o usuario atual. Sem sudo, sem tocar em /etc, sem
# escrever na configuracao do Claude Code de ninguem.
#
# O que faz, e so isso:
#   1. um symlink em $PREFIX/bin/plow-uso -> bin/emitir-uso.py deste repo
#   2. imprime a linha para carregar o plugin
#
# Desinstalar remove SO o symlink, e so se ele apontar para dentro deste repo.
set -e

REPO=$(cd "$(dirname "$0")" && pwd)
PREFIX="${PREFIX:-$HOME/.local}"
ACAO=instalar

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --prefix=*) PREFIX="${1#--prefix=}"; shift ;;
    --desinstalar) ACAO=desinstalar; shift ;;
    --check) ACAO=check; shift ;;
    -h|--help)
      echo "uso: $0 [--prefix DIR] [--desinstalar] [--check]"
      echo "     PREFIX padrao: \$HOME/.local"
      exit 0 ;;
    *) echo "opcao desconhecida: $1" >&2; exit 2 ;;
  esac
done

ALVO="$PREFIX/bin/plow-uso"
FONTE="$REPO/bin/emitir-uso.py"

no_path() {
  case ":$PATH:" in *":$PREFIX/bin:"*) return 1 ;; *) return 0 ;; esac
}

case "$ACAO" in

check)
  falhou=0
  if command -v python3 >/dev/null 2>&1; then
    echo "  ok    python3: $(python3 -V 2>&1)"
  else
    echo "  FALTA python3 -- obrigatorio"; falhou=1
  fi
  if command -v claude >/dev/null 2>&1; then
    echo "  ok    claude: no PATH"
  else
    echo "  aviso claude nao esta no PATH (so o teste fim-a-fim precisa dele)"
  fi
  if [ -L "$ALVO" ] || [ -e "$ALVO" ]; then
    echo "  ok    instalado: $ALVO"
  else
    echo "  ---   nao instalado ainda: $ALVO"
  fi
  if no_path; then
    echo "  aviso $PREFIX/bin NAO esta no PATH"
  else
    echo "  ok    $PREFIX/bin esta no PATH"
  fi
  if python3 "$REPO/hooks/agent-route.py" --check >/dev/null 2>&1; then
    echo "  ok    autoteste do hook"
  else
    echo "  FALHA autoteste do hook"; falhou=1
  fi
  if python3 "$FONTE" --check >/dev/null 2>&1; then
    echo "  ok    autoteste do emissor"
  else
    echo "  FALHA autoteste do emissor"; falhou=1
  fi
  if python3 "$REPO/hooks/gate-destrutivo.py" --check >/dev/null 2>&1; then
    echo "  ok    autoteste do gate destrutivo"
  else
    echo "  FALHA autoteste do gate destrutivo"; falhou=1
  fi
  if python3 "$REPO/bin/preflight.py" --check >/dev/null 2>&1; then
    echo "  ok    autoteste do preflight"
  else
    echo "  FALHA autoteste do preflight"; falhou=1
  fi
  exit $falhou
  ;;

desinstalar)
  if [ ! -L "$ALVO" ]; then
    echo "nada a remover: $ALVO nao e um symlink nosso"
    exit 0
  fi
  destino=$(readlink -f "$ALVO" 2>/dev/null || true)
  case "$destino" in
    "$REPO"/*) rm -f "$ALVO"; echo "removido: $ALVO" ;;
    *) echo "RECUSADO: $ALVO aponta para $destino, fora deste repo." >&2
       echo "Nao removo o que nao instalei." >&2
       exit 1 ;;
  esac
  exit 0
  ;;

instalar)
  command -v python3 >/dev/null 2>&1 || { echo "python3 e obrigatorio" >&2; exit 1; }
  [ -f "$FONTE" ] || { echo "nao achei $FONTE" >&2; exit 1; }

  mkdir -p "$PREFIX/bin"
  ln -sf "$FONTE" "$ALVO"
  chmod +x "$FONTE"
  echo "instalado: $ALVO -> $FONTE"

  if no_path; then
    echo
    echo "  $PREFIX/bin NAO esta no seu PATH. Adicione:"
    echo "    echo 'export PATH=\"$PREFIX/bin:\$PATH\"' >> ~/.bashrc"
  fi

  echo
  echo "Para carregar a camada no Claude Code:"
  echo "    claude --plugin-dir $REPO"
  echo
  echo "Para deixar permanente, um alias:"
  echo "    echo \"alias claude='claude --plugin-dir $REPO'\" >> ~/.bashrc"
  echo
  echo "Verificar:   $0 --check"
  echo "Desinstalar: $0 --desinstalar"
  ;;
esac
