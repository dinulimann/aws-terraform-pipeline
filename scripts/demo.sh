#!/usr/bin/env bash
# LocalStack up -> terraform apply -> upload sample day -> report.
set -euo pipefail
cd "$(dirname "$0")/.."
TF=${TERRAFORM:-terraform}
docker compose up -d
until curl -sf localhost:4566/_localstack/health >/dev/null; do sleep 2; done
$TF -chdir=envs/local init -input=false >/dev/null
$TF -chdir=envs/local apply -auto-approve -input=false -no-color | grep "Apply complete"
TERRAFORM=$TF python scripts/demo.py "$@"
