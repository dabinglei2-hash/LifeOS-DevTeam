[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$LocalPort = 8765
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$sensitive = '(?i)(^|_)(TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|ACCESS_?KEY|KEY|AUTH|CREDENTIAL)S?($|_)'
Get-ChildItem Env: | Where-Object { $_.Name -match $sensitive } | ForEach-Object {
    Remove-Item -LiteralPath ("Env:" + $_.Name)
}

$sourceRoot = Split-Path -Parent $PSScriptRoot
$dataRoot = Join-Path (Split-Path -Parent $sourceRoot) 'LADS-Workspace\control-center-data'
$venvPython = Join-Path $dataRoot 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw "Control Center external venv is missing."
}

Push-Location $sourceRoot
try {
    & $venvPython '-B' '-m' 'control_center.manager' 'stop' '--port' "$LocalPort"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
