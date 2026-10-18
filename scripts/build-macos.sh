#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != Darwin ]]; then
    echo 'Este script debe ejecutarse en macOS.' >&2
    exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
    echo 'Instala uv para preparar automaticamente Python y las dependencias de compilacion.' >&2
    exit 1
fi
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
exec uv run --no-project --isolated --python 3.13 \
    --with-requirements scripts/requirements-build.txt \
    python scripts/build.py --platform macos "$@"
