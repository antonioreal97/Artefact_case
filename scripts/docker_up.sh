#!/usr/bin/env bash
# Docker + Compose are the only application dependencies on the host.
set -euo pipefail

smoke=false
case "${1:-}" in
  --smoke) smoke=true ;;
  '') ;;
  *) printf 'Uso: %s [--smoke]\n' "$0" >&2; exit 2 ;;
esac
if [ "$#" -gt 1 ]; then
  printf 'Uso: %s [--smoke]\n' "$0" >&2
  exit 2
fi
cd "$(dirname "$0")/.."
if ! docker compose version >/dev/null 2>&1; then
  printf 'Docker Compose v2 é necessário. Instale/inicie o Docker e tente novamente.\n' >&2
  exit 1
fi
if [ ! -d 'Desafio Técnico AI Engineer 2026/data' ]; then
  printf 'Dados ausentes: coloque os CSVs e o PDF em Desafio Técnico AI Engineer 2026/data/.\n' >&2
  exit 1
fi
umask 077
if [ ! -f .env ]; then
  cp .env.example .env
fi
chmod 600 .env 2>/dev/null || true

docker compose build api
key_present="$(docker compose run --rm --no-deps -T api python -c 'import os; print(int(bool(os.environ.get("SEED_API_KEY", "").strip())))')"
if [ "$key_present" = 0 ]; then
  seed_key="$(docker compose run --rm --no-deps -T api python -c 'from app.core.security import generate_api_key; print(generate_api_key()[0])')"
  printf '\nSEED_API_KEY=%s\n' "$seed_key" >> .env
  unset seed_key
  printf 'Chave administrativa criada em .env (valor não exibido).\n'
elif [ "$key_present" != 1 ]; then
  printf 'Não foi possível verificar SEED_API_KEY no container.\n' >&2
  exit 1
fi

docker compose up -d db redis
docker compose --profile seed run --rm seed
docker compose up -d api worker
# Check from inside the API container: no host curl/Python installation is required.
if ! docker compose exec -T api python - <<'PY'
import time
import urllib.error
import urllib.request

end = time.monotonic() + 90
while time.monotonic() < end:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3) as response:
            if response.status == 200:
                break
    except (OSError, urllib.error.URLError):
        pass
    time.sleep(2)
else:
    raise SystemExit(1)
PY
then
  printf 'A API não ficou saudável em 90 segundos. Consulte: docker compose logs api\n' >&2
  exit 1
fi

if [ "$smoke" = true ]; then
  docker compose exec -T api python - <<'PY'
import json
import os
import urllib.error
import urllib.request

request = urllib.request.Request(
    "http://127.0.0.1:8000/v1/chat/messages",
    data=json.dumps({"message": "Quais opções de violões disponíveis custando até R$1000?"}).encode(),
    headers={"X-API-Key": os.environ["SEED_API_KEY"], "Content-Type": "application/json"},
)
try:
    with urllib.request.urlopen(request, timeout=90) as response:
        status, payload = response.status, json.load(response)
except (OSError, ValueError, urllib.error.URLError):
    raise SystemExit("Smoke falhou; consulte: docker compose logs api") from None
if status != 200 or "R$" not in payload.get("reply", ""):
    raise SystemExit("Smoke falhou: esperado HTTP 200 e resposta contendo R$.")
print("Smoke OK: HTTP 200; resposta contém R$.")
PY
fi

address="$(docker compose port api 8000)"
printf '\nAplicação pronta: http://%s (documentação: /docs)\n' "$address"
cat <<'NEXT'
Carregue a chave sem imprimi-la (Bash/WSL; execute na raiz do repositório):
  export SEED_API_KEY="$(grep '^SEED_API_KEY=' .env | tail -n 1 | cut -d= -f2-)"
A chave é administrativa: mantenha-a no terminal/servidor confiável.
NEXT
printf '  curl --fail-with-body --silent --show-error http://%s/v1/chat/messages \\\n' "$address"
cat <<'NEXT'
    -H "X-API-Key: $SEED_API_KEY" -H "Content-Type: application/json" \
    -d '{"message":"Quais opções de violões disponíveis custando até R$1000?"}'

Parar: docker compose down
Apagar TODOS os dados deste projeto: docker compose down -v
Depois do reset, execute este script novamente: o seed provisiona a mesma chave de .env.
Uma chave diferente é rejeitada em um banco já inicializado; preserve .env.
Mantenha COMPOSE_PROJECT_NAME e as portas configuradas ao executar esses comandos.
NEXT
