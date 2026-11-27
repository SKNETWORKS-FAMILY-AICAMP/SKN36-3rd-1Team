# Issue #122 follow-up: read-only R3D runner for tests/manual/verify_issue122_followup.py.
# - Reuses the existing local DPAPI secret files (current Windows user only). Values are never printed or written;
#   they live in process environment variables only and are restored in finally.
# - Search (read) only. Does not start containers, touch volumes, or edit .env.
# - Real LLM calls happen only with -RealLlm (3 fixed cases in the recorder).
param(
  [switch]$RealLlm,
  [string]$Out,
  [string]$SecretDir = 'C:\dev\project\SKN36-3rd-1Team\restore-bundle\secrets-01',
  [string]$PythonExecutable = 'C:\dev\project\SKN36-3rd-1Team\SKN36-3rd-1Team\.venv\Scripts\python.exe',
  [string]$EnvFilePath = 'C:\dev\project\SKN36-3rd-1Team\SKN36-3rd-1Team\.env'
)
$ErrorActionPreference = 'Stop'
$worktree = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$names = 'NEO4J_URI', 'NEO4J_USER', 'NEO4J_PASSWORD', 'QDRANT_URL', 'QDRANT_API_KEY',
         'OLLAMA_BASE_URL', 'LLM_TIMEOUT_SECONDS', 'LLM_MAX_OUTPUT_TOKENS'
$previous = @{}
foreach ($n in $names) { $previous[$n] = [Environment]::GetEnvironmentVariable($n, 'Process') }

function Read-R3Secret([string]$path) {
  $s = Import-Clixml -Path $path; $p = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
  try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($p) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($p); $s.Dispose() }
}

Push-Location $worktree
try {
  $mode = 'mock'; $extra = @()
  if ($RealLlm) { $mode = 'llm'; $extra = @('--llm') }
  if (-not $Out) { $Out = "docs/reports/issue122-followup-r3d-$mode-$(Get-Date -Format 'yyyyMMdd-HHmmss').json" }
  # Refuse an existing output before reading secrets or calling any service.
  if (Test-Path -LiteralPath $Out) { throw 'Output file already exists. Pass a new -Out path.' }
  $neo4jSecret = Join-Path $SecretDir 'neo4j.clixml'
  $qdrantSecret = Join-Path $SecretDir 'qdrant.clixml'
  foreach ($f in @($neo4jSecret, $qdrantSecret)) {
    if (-not (Test-Path -LiteralPath $f -PathType Leaf)) { throw 'Secret file missing. Check -SecretDir.' }
  }
  $env:NEO4J_URI      = 'bolt://127.0.0.1:56787'
  $env:NEO4J_USER     = 'neo4j'
  $env:NEO4J_PASSWORD = Read-R3Secret $neo4jSecret
  $env:QDRANT_URL     = 'http://127.0.0.1:56756'
  $env:QDRANT_API_KEY = Read-R3Secret $qdrantSecret
  if (-not $env:NEO4J_PASSWORD -or -not $env:QDRANT_API_KEY) { throw 'Could not decrypt secrets (different Windows user?). Stopping.' }
  $env:OLLAMA_BASE_URL = 'http://127.0.0.1:11434'
  $env:LLM_TIMEOUT_SECONDS = '45'
  $env:LLM_MAX_OUTPUT_TOKENS = '1600'
  & $PythonExecutable -B tests\manual\verify_issue122_followup.py --env-file $EnvFilePath --out $Out @extra
  Write-Host "exit code: $LASTEXITCODE"
} finally {
  foreach ($n in $names) { [Environment]::SetEnvironmentVariable($n, $previous[$n], 'Process') }
  Pop-Location
}
