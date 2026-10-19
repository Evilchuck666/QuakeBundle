$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') {
    throw 'This script must be run on Windows.'
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'Install uv to automatically set up Python and the build dependencies.'
}
Push-Location (Join-Path $PSScriptRoot '..')
try {
    & uv run --no-project --isolated --python 3.13 `
        --with-requirements scripts/requirements-build.txt `
        python scripts/build.py --platform windows @args
    $buildResult = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $buildResult
