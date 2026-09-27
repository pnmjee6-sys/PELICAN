#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
proof_venv="$(mktemp -d)/context-passport-venv"
trap 'rm -rf "${proof_venv%/context-passport-venv}"' EXIT

python3.11 -m venv "$proof_venv"
"$proof_venv/bin/python" -m pip install --disable-pip-version-check -r "$repo_dir/backend/requirements.lock"
PYTHONPATH="$repo_dir/backend" "$proof_venv/bin/python" -m pytest "$repo_dir/backend/tests"
PYTHONPATH="$repo_dir/backend" "$proof_venv/bin/python" -c 'import fastapi, google.genai, mem0, pymongo; print("clean reinstall imports: ok")'
