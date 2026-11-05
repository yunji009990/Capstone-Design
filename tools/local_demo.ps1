param([ValidateSet('setup','setup-web','init','setup-models','import-server','import-web','prepare-friend','start','start-web','open-web','stop','status','use-local','use-server')]
      [string]$Action = 'status')
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$repoRoot = Split-Path -Parent $PSScriptRoot
$demoPython = Join-Path $repoRoot 'tools/_work/local_demo/venv/Scripts/python.exe'
$demoScript = Join-Path $PSScriptRoot 'local_demo.py'
if ($Action -eq 'setup') {
    if (-not (Test-Path -LiteralPath $demoPython)) {
        py -3.11 -m venv (Join-Path $repoRoot 'tools/_work/local_demo/venv')
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 환경을 만들지 못했습니다.' }
    }
    & $demoPython -m pip install --disable-pip-version-check torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
    if ($LASTEXITCODE -ne 0) { throw 'CUDA 패키지 설치 실패' }
    & $demoPython -m pip install --disable-pip-version-check -r (Join-Path $repoRoot 'Server/requirements-local-demo.txt')
    if ($LASTEXITCODE -ne 0) { throw '로컬 실행 패키지 설치 실패' }
    & $demoPython $demoScript init
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $demoPython $demoScript setup-models
} elseif ($Action -eq 'setup-web') {
    $webVenv = Join-Path $repoRoot 'tools/_work/local_demo/web-venv'
    $webPython = Join-Path $webVenv 'Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $webPython)) {
        py -3.11 -m venv $webVenv
        if ($LASTEXITCODE -ne 0) { throw '웹 실행 환경을 만들지 못했습니다.' }
    }
    & $webPython -X utf8 -m pip install --disable-pip-version-check -r (Join-Path $repoRoot 'Server/requirements-local-web.txt')
    if ($LASTEXITCODE -ne 0) { throw '로컬 웹 패키지 설치 실패' }
} else {
    if (-not (Test-Path -LiteralPath $demoPython)) { throw '먼저 tools/local_demo.ps1 setup을 실행하세요.' }
    & $demoPython $demoScript $Action
}
exit $LASTEXITCODE
