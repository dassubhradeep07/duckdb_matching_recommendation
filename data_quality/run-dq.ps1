param(
    [ValidateSet('raw', 'master', 'all')][string]$Layer = 'all',
    [string]$Config,
    [string]$OutputDir,
    [switch]$FailOnQuality
)
$ErrorActionPreference = 'Stop'
$python = Join-Path (Split-Path -Parent $PSScriptRoot) '.venv-dq\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Run data_quality\setup.ps1 first to install Soda into its separate environment.'
}
$dqArgs = @((Join-Path $PSScriptRoot 'run.py'), '--layer', $Layer)
if ($Config) { $dqArgs += @('--config', $Config) }
if ($OutputDir) { $dqArgs += @('--output-dir', $OutputDir) }
if ($FailOnQuality) { $dqArgs += '--fail-on-quality' }
& $python @dqArgs
exit $LASTEXITCODE
