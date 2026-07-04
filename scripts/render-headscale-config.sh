#!/usr/bin/env bash
# Generate headscale/config.yaml from the template + .env, and make sure the
# headscale data dir exists with a valid (possibly empty) extra-records file.
# Idempotent; called automatically by start.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source scripts/lib.sh
load_env

sed "s/@MESH_HOST@/${MESH_HOST}/g" headscale/config.template.yaml > headscale/config.yaml

mkdir -p headscale/data
[ -f headscale/data/extra_records.json ] || echo "[]" > headscale/data/extra_records.json
