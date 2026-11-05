<#
scripts/gate.ps1 · 本地一键闸门（串行、单进程、无浏览器）

给「本地低频窗口」用的一把梭：node 单测 → biome → pytest → 发布物校验。
刻意区别于 ci.yml：这里不跑 python-scripts（要起 mock 服务）、不碰 Docker、
不起 headless 浏览器 / AVD / Gradle，避免本地资源占用过高。

用法：
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/gate.ps1
  ... -SkipNode / -SkipPython   # 只跑其中一半时用

退出码：全部通过 0；有任何一步失败 1（并打印该步日志末尾）。
日志：artifacts/gate/<step>.log
#>
param(
  [switch]$SkipNode,
  [switch]$SkipPython
)

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
Set-Location -LiteralPath $root

$logDir = Join-Path $root 'artifacts/gate'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# ---------- 解释器探测 ----------
$py = $null
foreach ($candidate in @(
    (Join-Path $root '.venv/Scripts/python.exe'),
    (Join-Path $root '.venv-win/Scripts/python.exe')
  )) {
  if (Test-Path -LiteralPath $candidate) { $py = $candidate; break }
}
if (-not $py) {
  $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
  if ($cmd) { $py = $cmd.Source }
}

$npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
if (-not $npm) { $npm = (Get-Command npm -ErrorAction SilentlyContinue).Source }

$script:results = @()

function Invoke-Step {
  param(
    [string]$Id,
    [string]$Title,
    [scriptblock]$Action
  )
  $log = Join-Path $logDir "$Id.log"
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  $lines = @(& $Action 2>&1 | ForEach-Object { "$_" })
  $code = if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE }
  $sw.Stop()
  Set-Content -LiteralPath $log -Value $lines -Encoding UTF8
  $script:results += [pscustomobject]@{
    Id      = $Id
    Title   = $Title
    Exit    = [int]$code
    Seconds = [math]::Round($sw.Elapsed.TotalSeconds, 1)
    Log     = $log
    Tail    = ($lines | Select-Object -Last 8)
  }
}

function Add-Skipped {
  param([string]$Id, [string]$Title, [string]$Reason)
  $script:results += [pscustomobject]@{
    Id = $Id; Title = $Title; Exit = 127; Seconds = 0; Log = $Reason; Tail = @($Reason)
  }
}

# ---------- 1) node 单测 ----------
if (-not $SkipNode) {
  if (-not $npm) {
    Add-Skipped -Id 'node' -Title 'node --test (web)' -Reason '未找到 npm，请先安装 Node（>=22）'
  }
  else {
    Invoke-Step -Id 'node' -Title 'node --test (web)' -Action {
      Push-Location -LiteralPath (Join-Path $root 'web')
      & $npm test
      $c = $LASTEXITCODE
      Pop-Location
      $global:LASTEXITCODE = $c
    }
  }
}

# ---------- 2) biome ----------
if (-not $SkipNode) {
  $biomeBin = Join-Path $root 'web/node_modules/.bin/biome.cmd'
  if (-not (Test-Path -LiteralPath $biomeBin) -and -not (Test-Path -LiteralPath (Join-Path $root 'web/node_modules/.bin/biome'))) {
    Add-Skipped -Id 'biome' -Title 'biome check' -Reason 'web/node_modules 未安装，先执行：cd web && npm ci'
  }
  elseif (-not $npm) {
    Add-Skipped -Id 'biome' -Title 'biome check' -Reason '未找到 npm'
  }
  else {
    Invoke-Step -Id 'biome' -Title 'biome check（白名单）' -Action {
      Push-Location -LiteralPath (Join-Path $root 'web')
      & $npm run -s lint
      $c = $LASTEXITCODE
      Pop-Location
      $global:LASTEXITCODE = $c
    }
  }
}

# ---------- 3) pytest ----------
if (-not $SkipPython) {
  if (-not $py) {
    Add-Skipped -Id 'pytest' -Title 'pytest server/tests' -Reason '未找到 python（建议先建 .venv 并装 pytest / pytest-subtests）'
  }
  else {
    Invoke-Step -Id 'pytest' -Title 'pytest server/tests' -Action {
      & $py -m pytest server/tests -q
      $global:LASTEXITCODE = $LASTEXITCODE
    }
  }
}

# ---------- 4) 发布物校验 ----------
# assets/www 是生成物、不入库（见 .gitignore）；干净 worktree 里先按 build_assets.sh
# 同步一次再校验（与 ci.yml 同口径），已存在时只校验，用来抓「工作树漂移」。
if (-not $SkipPython) {
  if (-not $py) {
    Add-Skipped -Id 'assets' -Title 'verify_assets.py' -Reason '未找到 python'
  }
  else {
    Invoke-Step -Id 'assets' -Title 'verify_assets.py（web ↔ assets/www）' -Action {
      $dst = Join-Path $root 'android/app/src/main/assets/www'
      if (-not (Test-Path -LiteralPath $dst)) {
        $bash = (Get-Command bash -ErrorAction SilentlyContinue).Source
        if (-not $bash) {
          Write-Output '[gate] assets/www 不存在且未找到 bash，无法同步发布物'
          $global:LASTEXITCODE = 127
          return
        }
        Write-Output '[gate] assets/www 不存在，先执行 bash scripts/build_assets.sh'
        & $bash scripts/build_assets.sh
        if ($LASTEXITCODE -ne 0) { $global:LASTEXITCODE = $LASTEXITCODE; return }
      }
      & $py scripts/verify_assets.py
      $global:LASTEXITCODE = $LASTEXITCODE
    }
  }
}

# ---------- 汇总表 ----------
Write-Host ''
Write-Host '================ 本地闸门结果 ================'
foreach ($r in $script:results) {
  $flag = if ($r.Exit -eq 0) { 'PASS' } else { 'FAIL' }
  '{0,-6} {1,-34} {2,8}s  {3}' -f $flag, $r.Title, $r.Seconds, $r.Log | Write-Host
}
Write-Host '=============================================='

$failed = $script:results | Where-Object { $_.Exit -ne 0 }
if ($failed) {
  Write-Host ''
  foreach ($r in $failed) {
    Write-Host ("---- 失败：{0}（exit {1}）----" -f $r.Title, $r.Exit)
    $r.Tail | ForEach-Object { Write-Host $_ }
    Write-Host ''
  }
  exit 1
}

Write-Host '全部闸门通过。'
exit 0
