$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$sourcePython = Join-Path $project '.venv\Scripts\python.exe'
$dqPython = Join-Path $project '.venv-dq\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $dqPython)) {
    if (-not (Test-Path -LiteralPath $sourcePython)) {
        throw "Project Python not found: $sourcePython. Create .venv with Python 3.10+ first."
    }
    & $sourcePython -m venv (Join-Path $project '.venv-dq')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the separate DQ environment.' }
}
& $dqPython -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install Soda DQ dependencies.' }
Write-Output 'DQ environment is ready. Run .\data_quality\run-dq.ps1 from the project root.'
