#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != Linux ]]; then
    echo 'This script must be run on GNU/Linux.' >&2
    exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
    echo 'Install uv to automatically set up Python and the build dependencies.' >&2
    exit 1
fi
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
exec uv run --no-project --isolated --python 3.13 \
    --with-requirements scripts/requirements-build.txt \
    python scripts/build.py --platform linux "$@"
