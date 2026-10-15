# One command: a private Python environment with the exact, hash-checked versions, then the first-run checks.
# powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path .venv\Scripts\python.exe)) {
    $py = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }
    & $py -c "import sys; sys.exit(sys.version_info < (3, 11))"
    if ($LASTEXITCODE -ne 0) { throw "Precisa do Python 3.11 ou mais novo (python.org)." }
    & $py -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Não consegui criar o .venv." }
}
& .venv\Scripts\python.exe -m pip install --disable-pip-version-check --require-hashes -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "A instalação dos pacotes falhou." }
& .venv\Scripts\python.exe onboarding.py
Write-Host "Pronto. Para usar: .venv\Scripts\python.exe hotkey_windows.py"
