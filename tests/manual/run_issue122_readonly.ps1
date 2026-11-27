# #122 R3D 읽기 전용 검증 실행기. 기존 #76 로컬 실행기의 인증 처리 재사용.
# - 인증값을 바꾸거나 새로 만들지 않는다. restore-bundle\secrets-01의 기존 DPAPI 파일을 현재 Windows 사용자로만 복호화한다.
# - 값은 출력·파일 저장하지 않고 프로세스 환경 변수로만 넣으며, finally에서 이전 설정을 복원한다.
# - DB에는 검색(읽기)만 한다. 컨테이너·볼륨·.env는 건드리지 않는다.
param(
  [switch]$RealLlm,
  [switch]$Smoke,
  [string]$Out,
  [string]$QdrantSecretPath,
  [string]$Neo4jSecretPath,
  [string]$PythonExecutable = 'C:\dev\project\SKN36-3rd-1Team\SKN36-3rd-1Team\.venv\Scripts\python.exe',
  [string]$EnvFilePath = 'C:\dev\project\SKN36-3rd-1Team\SKN36-3rd-1Team\.env'
)
$ErrorActionPreference = 'Stop'
$secretDir = 'C:\dev\project\SKN36-3rd-1Team\restore-bundle\secrets-01'
$worktree = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$python = $PythonExecutable
$envFile = $EnvFilePath
if (-not $QdrantSecretPath) { $QdrantSecretPath = Join-Path $secretDir 'qdrant.clixml' }
if (-not $Neo4jSecretPath) { $Neo4jSecretPath = Join-Path $secretDir 'neo4j.clixml' }
$names     = 'NEO4J_URI', 'NEO4J_USER', 'NEO4J_PASSWORD', 'QDRANT_URL', 'QDRANT_API_KEY'
$names += 'OLLAMA_BASE_URL', 'LLM_TIMEOUT_SECONDS', 'LLM_MAX_OUTPUT_TOKENS', 'RUN_FINAL_V140_SMOKE'
$previous = @{}
foreach ($n in $names) { $previous[$n] = [Environment]::GetEnvironmentVariable($n, 'Process') }

function Read-R3Secret([string]$path) {
  $s = Import-Clixml -Path $path; $p = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
  try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($p) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($p); $s.Dispose() }
}

Push-Location $worktree
try {
  $extra = @()
  $mode = 'mock'
  if ($RealLlm) { $extra = @('--llm'); $mode = 'llm' }
  if (-not $Out) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $Out = "docs/reports/issue122-r3d-$mode-$stamp.json"
  }
  # 이미 있는 결과는 인증 파일을 읽거나 외부 서비스를 호출하기 전에 거부한다.
  if (-not $Smoke -and (Test-Path -LiteralPath $Out)) { throw '결과 파일이 이미 있습니다. 새 -Out 경로를 지정해 주세요.' }
  foreach ($f in @($Neo4jSecretPath, $QdrantSecretPath)) {
    if (-not (Test-Path -LiteralPath $f -PathType Leaf)) { throw '인증 파일이 없습니다. -Neo4jSecretPath 및 -QdrantSecretPath를 확인해 주세요.' }
  }
  $env:NEO4J_URI      = 'bolt://127.0.0.1:56787'
  $env:NEO4J_USER     = 'neo4j'
  $env:NEO4J_PASSWORD = Read-R3Secret $Neo4jSecretPath
  $env:QDRANT_URL     = 'http://127.0.0.1:56756'
  $env:QDRANT_API_KEY = Read-R3Secret $QdrantSecretPath
  if (-not $env:NEO4J_PASSWORD -or -not $env:QDRANT_API_KEY) { throw '인증 파일을 복호화하지 못했습니다(다른 Windows 사용자일 수 있음) → 중단' }
  $env:OLLAMA_BASE_URL = 'http://127.0.0.1:11434'
  $env:LLM_TIMEOUT_SECONDS = '45'
  $env:LLM_MAX_OUTPUT_TOKENS = '1600'
  if ($Smoke) {
    $env:RUN_FINAL_V140_SMOKE = '1'
    & $python -B -m pytest tests/integration/test_final_v140_smoke.py -q --tb=short
  } else {
    & $python -B tests\manual\verify_issue122_consistency.py --env-file $envFile --out $Out @extra
  }
  Write-Host "exit code: $LASTEXITCODE"
} finally {
  foreach ($n in $names) { [Environment]::SetEnvironmentVariable($n, $previous[$n], 'Process') }
  Pop-Location
}

