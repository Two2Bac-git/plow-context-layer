#!/bin/sh
# Re-verifica a alegacao central deste repo. Dois niveis: autoteste e fim-a-fim.
# O autoteste NAO substitui o fim-a-fim: print do proprio script nao e prova.
set -e
cd "$(dirname "$0")"

echo "== 1. autoteste =="
python3 hooks/agent-route.py --check

echo
echo "== 2. fim-a-fim =="
if ! command -v claude >/dev/null 2>&1; then
  echo "  PULADO: 'claude' nao esta no PATH."
  echo "  Sem ele nao da para provar que a marca entra no subagente."
  exit 0
fi

r=$(claude -p "$(cat prompt-e2e.txt)" --plugin-dir "$(pwd)" \
      --dangerously-skip-permissions < /dev/null 2>&1 | tail -1)
echo "  $r"
case "$r" in
  *SUBAGENTE=PRESENTE*)
    echo "  PASSOU: a marca atravessou para dentro do subagente." ;;
  *)
    echo "  FALHOU: a marca nao chegou. Esta e a alegacao central do repo."
    exit 1 ;;
esac
