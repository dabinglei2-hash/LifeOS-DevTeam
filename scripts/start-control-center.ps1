[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$LocalPort = 8765,
    [switch]$NoBrowser,
    [switch]$InstallDependencies
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
    if (-not $InstallDependencies) {
        throw "Control Center venv is missing. Re-run with -InstallDependencies."
    }
    $systemPython = (Get-Command python -ErrorAction Stop).Source
    Push-Location $sourceRoot
    try {
        & $systemPython '-B' '-m' 'control_center.manager' 'bootstrap'
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    finally {
        Pop-Location
    }
}

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw "Control Center external venv was not created."
}

$managerArgs = @('-B', '-m', 'control_center.manager', 'start', '--port', "$LocalPort")
if ($NoBrowser) { $managerArgs += '--no-browser' }
if ($InstallDependencies) { $managerArgs += '--install-dependencies' }

Push-Location $sourceRoot
try {
    & $venvPython @managerArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
