$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') {
    throw 'Este script debe ejecutarse en Windows.'
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'Instala uv para preparar automaticamente Python y las dependencias de compilacion.'
}
Push-Location $PSScriptRoot
try {
    & uv run --no-project --isolated --python 3.13 `
        --with-requirements requirements-build.txt `
        python scripts/build.py --platform windows @args
    $buildResult = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $buildResult
