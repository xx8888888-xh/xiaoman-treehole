param(
  [ValidateSet('start','stop','status','guard','test-window','dryrun')]
  [string]$Action = 'status',
  [string]$Avd = 'treehole_api34',
  [string]$Gpu = 'swiftshader_indirect',
  [int]$Cores = 4,
  [int]$MemoryMb = 3072,
  [int]$BootTimeoutSec = 240,
  [switch]$StopWatchdog,
  [switch]$ViewOnly,
  [switch]$UpdateBaseline,
  [string]$ShotDir = ''
)
$ErrorActionPreference = 'Continue'
$env:ANDROID_HOME = 'D:\android-sdk'
$env:ANDROID_SDK_ROOT = 'D:\android-sdk'
$env:ANDROID_AVD_HOME = 'D:\android-avd'
$env:ANDROID_USER_HOME = 'D:\android-avd\user-home'
$env:ANDROID_EMULATOR_HOME = 'D:\android-avd\user-home'
$emu = 'D:\android-sdk\emulator\emulator.exe'
$ps = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
$adb = 'D:\android-sdk\platform-tools\adb.exe'
$watchdog = 'D:\android-avd\emulator_watchdog.ps1'
$log = 'D:\android-avd\avd_local.log'
$shot = 'D:\android-avd\avd_last.png'
$coresTotal = (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors

# ---- 基线策略：云端 720x1280 是唯一像素/几何基线源，本地降配只允许目视 ----
$repoRoot = ''
if ($PSScriptRoot) { $repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }
if (-not $repoRoot -or -not (Test-Path (Join-Path $repoRoot '.git'))) {
  $repoRoot = 'D:\我的\编程\vibe coding tools\treehole-ci'
}
$protectedDirs = @('docs\shots', 'docs\ui-shots-r2', 'docs\android-battery\shots')

# 启动时段 21:00-06:00（其余时段本地不开）；30% 硬线始终生效
$timeMod = 'D:\android-avd\emu_timewindow.ps1'
if (-not (Test-Path -LiteralPath $timeMod)) { $timeMod = Join-Path $PSScriptRoot 'emu_timewindow.ps1' }
. $timeMod
$limitPercent = 30
$graceSeconds = 120
$intervalSeconds = 2
function Get-LimitLine { "LIMIT=on threshold=${limitPercent}% grace=${graceSeconds}s interval=${intervalSeconds}s start_window=21:00-06:00 now=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" }
function Get-StartGate { if (Test-QuietWindow (Get-Date)) { return 'START=allowed' } else { return 'START=blocked outside=21:00-06:00' } }
function Get-LaunchArgs { @('-avd', $Avd, '-no-audio', '-no-boot-anim', '-gpu', $Gpu, '-no-metrics', '-cores', "$Cores", '-memory', "$MemoryMb", '-no-snapshot-load', '-no-snapshot-save') }

function Get-AvdLcd([string]$avdName) {
  $ini = Join-Path 'D:\android-avd' "$avdName.avd\config.ini"
  $r = [pscustomobject]@{ width = 0; height = 0; density = 0 }
  if (-not (Test-Path $ini)) { return $r }
  foreach ($line in Get-Content -LiteralPath $ini) {
    if ($line -match '^\s*hw\.lcd\.width\s*=\s*(\d+)') { $r.width = [int]$Matches[1] }
    elseif ($line -match '^\s*hw\.lcd\.height\s*=\s*(\d+)') { $r.height = [int]$Matches[1] }
    elseif ($line -match '^\s*hw\.lcd\.density\s*=\s*(\d+)') { $r.density = [int]$Matches[1] }
  }
  return $r
}

$lcd = Get-AvdLcd $Avd
$deviceLcd = $null
try {
  $wmOut = (& $adb shell wm size 2>$null | Out-String)
  if ($wmOut -match 'Override size:\s*(\d+)x(\d+)') { $deviceLcd = [pscustomobject]@{ width = [int]$Matches[1]; height = [int]$Matches[2] } }
  elseif ($wmOut -match 'Physical size:\s*(\d+)x(\d+)') { $deviceLcd = [pscustomobject]@{ width = [int]$Matches[1]; height = [int]$Matches[2] } }
} catch { $deviceLcd = $null }
$viewOnly = $false
$viewReason = ''
if ($ViewOnly) { $viewOnly = $true; $viewReason = 'forced' }
elseif ($lcd.width -le 0 -or $lcd.height -le 0) { $viewOnly = $true; $viewReason = 'lcd-unknown' }
elseif ($lcd.width -lt 720 -or $lcd.height -lt 1280) { $viewOnly = $true; $viewReason = "lcd=$($lcd.width)x$($lcd.height)" }
elseif ($deviceLcd -and ($deviceLcd.width -lt 720 -or $deviceLcd.height -lt 1280)) {
  $viewOnly = $true; $viewReason = "device=$($deviceLcd.width)x$($deviceLcd.height)"
}

function Get-ModeLine {
  $mode = if ($viewOnly) { 'viewonly' } else { 'baseline' }
  $writes = if ($viewOnly) { 'REFUSED' } else { 'allowed' }
  $lcdText = if ($lcd.width -gt 0) { "$($lcd.width)x$($lcd.height)@$($lcd.density)" } else { 'unknown' }
  return "MODE=$mode avd=$Avd lcd=$lcdText cores=$Cores gpu=$Gpu snapshot=none baseline_writes=$writes reason=$viewReason"
}

function Test-ProtectedPath([string]$p) {
  $full = [IO.Path]::GetFullPath($(if ([IO.Path]::IsPathRooted($p)) { $p } else { Join-Path $repoRoot $p }))
  foreach ($d in $protectedDirs) {
    $dp = [IO.Path]::GetFullPath((Join-Path $repoRoot $d))
    if ($full.StartsWith($dp, [StringComparison]::OrdinalIgnoreCase)) { return $d }
  }
  return ''
}

function Assert-BaselineGuard {
  if (-not $viewOnly) { return }
  if ($UpdateBaseline) {
    Write-Log "REFUSED baseline write: --update-baseline in MODE=viewonly ($viewReason)"
    "REFUSED baseline write: --update-baseline is not allowed in MODE=viewonly ($viewReason); 720x1280 baselines are produced on cloud only."
    exit 2
  }
  if ($ShotDir) {
    $hit = Test-ProtectedPath $ShotDir
    if ($hit) {
      Write-Log "REFUSED baseline write: ShotDir=$ShotDir -> $hit in MODE=viewonly ($viewReason)"
      "REFUSED baseline write: $ShotDir -> $hit is not allowed in MODE=viewonly ($viewReason); write local shots outside docs/shots."
      exit 2
    }
    Write-Log "viewonly shot dir accepted (non-baseline): $ShotDir"
  }
}

function Write-Log([string]$msg) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg" | Add-Content -LiteralPath $log -Encoding UTF8 }

function Test-Watchdog {
  $pidPath = 'D:\android-avd\watchdog.pid'
  if (-not (Test-Path $pidPath)) { return $false }
  $wpid = (Get-Content $pidPath -ErrorAction SilentlyContinue | Select-Object -First 1)
  if (-not $wpid) { return $false }
  $p = Get-CimInstance Win32_Process -Filter "ProcessId=$wpid" -ErrorAction SilentlyContinue
  return ($p -and $p.CommandLine -match 'emulator_watchdog\.ps1')
}

function Start-Watchdog {
  if (Test-Watchdog) { Write-Log 'watchdog already running'; return }
  Remove-Item -LiteralPath 'D:\android-avd\watchdog.stop' -Force -ErrorAction SilentlyContinue
  $cmd = "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$watchdog`""
  Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd } | Out-Null
  $ok = $false
  for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Watchdog) { $ok = $true; break }
  }
  Write-Log "watchdog started: $ok"
}

function Stop-Watchdog {
  New-Item -ItemType File -Path 'D:\android-avd\watchdog.stop' -Force | Out-Null
  Start-Sleep -Seconds 3
  $p = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'emulator_watchdog\.ps1' }
  foreach ($x in $p) { Stop-Process -Id $x.ProcessId -Force -ErrorAction SilentlyContinue }
  Remove-Item -LiteralPath 'D:\android-avd\watchdog.stop' -Force -ErrorAction SilentlyContinue
  Write-Log 'watchdog stopped'
}

function Get-Group {
  Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('emulator.exe','qemu-system-x86_64.exe') }
}

function Get-NonBlackRatio([string]$path) {
  try {
    Add-Type -AssemblyName System.Drawing
    $bmp = [System.Drawing.Bitmap]::FromFile($path)
    $w = $bmp.Width; $h = $bmp.Height
    $step = [math]::Max(1, [math]::Floor([math]::Min($w,$h) / 64))
    $tot = 0; $lit = 0
    for ($y = 0; $y -lt $h; $y += $step) {
      for ($x = 0; $x -lt $w; $x += $step) {
        $c = $bmp.GetPixel($x,$y)
        $v = [math]::Max($c.R, [math]::Max($c.G,$c.B))
        $tot++
        if ($v -ge 20) { $lit++ }
      }
    }
    $bmp.Dispose()
    return [pscustomobject]@{ ratio = [math]::Round($lit / [math]::Max($tot,1), 4); size = "${w}x${h}" }
  } catch {
    return [pscustomobject]@{ ratio = -1; size = "err:$($_.Exception.Message)" }
  }
}

if ($Action -eq 'test-window') {
  # 时间窗边界自检：固定日期，跨零点用次日
  $cases = @(
    @{ t = '2026-11-07 20:59:00'; expect = 'LIMIT=on' },
    @{ t = '2026-11-07 21:00:00'; expect = 'LIMIT=off' },
    @{ t = '2026-11-07 23:59:00'; expect = 'LIMIT=off' },
    @{ t = '2026-11-08 00:30:00'; expect = 'LIMIT=off' },
    @{ t = '2026-11-08 05:59:00'; expect = 'LIMIT=off' },
    @{ t = '2026-11-08 06:00:00'; expect = 'LIMIT=on' },
    @{ t = '2026-11-08 06:01:00'; expect = 'LIMIT=on' }
  )
  $fail = 0
  foreach ($c in $cases) {
    $dt = [datetime]::ParseExact($c.t, 'yyyy-MM-dd HH:mm:ss', $null)
    $got = Get-LimitLabel $dt
    $ok = if ($got -eq $c.expect) { 'PASS' } else { $fail++; 'FAIL' }
    "$($c.t) -> $got (expect $($c.expect)) $ok"
  }
  if ($fail -gt 0) { "WINDOW TEST FAIL count=$fail"; exit 1 }
  'WINDOW TEST PASS 7/7'
  exit 0
}

if ($Action -eq 'dryrun') {
  # 不启动模拟器：打印模式、启动闸门、完整启动命令与看门狗阈值
  Get-ModeLine
  Get-LimitLine
  Get-StartGate
  "CMD=$emu $((Get-LaunchArgs) -join ' ')"
  "WATCHDOG=$ps -NoProfile -ExecutionPolicy Bypass -File D:\android-avd\emulator_watchdog.ps1 -LimitPercent $limitPercent -GraceSeconds $graceSeconds -IntervalSeconds $intervalSeconds"
  "LOGS=D:\android-avd\watchdog.log,D:\android-avd\avd_local.log,D:\android-avd\avd_local.out.log"
  exit 0
}

if ($Action -eq 'guard') {
  # 供走查脚本在写截图前调用：viewonly 下拒绝任何落到基线目录的路径
  Get-ModeLine
  Get-LimitLine
  if ($ShotDir) {
    $hit = Test-ProtectedPath $ShotDir
    if ($hit -and $viewOnly) {
      "REFUSED baseline write: $ShotDir -> $hit in MODE=viewonly ($viewReason)"
      exit 2
    }
    "GUARD OK: $ShotDir allowed"
  }
  exit 0
}

if ($Action -eq 'status') {
  Get-ModeLine
  Get-LimitLine
  Get-StartGate
  $g = Get-Group
  $g | Select-Object ProcessId,Name,CreationDate,CommandLine | Format-List
  "watchdog: $(Test-Watchdog)"
  exit 0
}

if ($Action -eq 'stop') {
  & $adb emu kill 2>&1 | Out-Null
  Start-Sleep -Seconds 8
  foreach ($p in Get-Group) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
  Start-Sleep -Seconds 4
  $left = Get-Group
  if ($StopWatchdog) { Stop-Watchdog }
  if ($left) {
    Write-Log "STOP FAILED residual=$($left.Count): $(($left | ForEach-Object { "$($_.ProcessId):$($_.Name)" }) -join ',')"
    "STOP FAILED: residual emulator/qemu processes"
    exit 1
  }
  Write-Log 'stop ok: no residual emulator/qemu'
  "STOP OK: no residual emulator/qemu"
  exit 0
}

# start
if (-not (Test-QuietWindow (Get-Date))) {
  Write-Log "start refused: outside 21:00-06:00 (now=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))"
  "START REFUSED: outside 21:00-06:00 (now=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')); local emulator may start only in 21:00-06:00"
  exit 3
}
Assert-BaselineGuard
Get-ModeLine
Get-LimitLine
Start-Watchdog
$left = Get-Group
if ($left) {
  foreach ($p in $left) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
  Start-Sleep -Seconds 4
}
& $adb start-server 2>&1 | Out-Null

$args = Get-LaunchArgs
Write-Log "launch avd=$Avd gpu=$Gpu cores=$Cores mem=$MemoryMb snapshot=none"
$sw = [Diagnostics.Stopwatch]::StartNew()
$proc = Start-Process -FilePath $emu -ArgumentList $args -RedirectStandardOutput 'D:\android-avd\avd_local.out.log' -RedirectStandardError 'D:\android-avd\avd_local.err.log' -PassThru

$deadline = (Get-Date).AddSeconds($BootTimeoutSec)
$booted = $false
while ((Get-Date) -lt $deadline) {
  $b = (& $adb shell getprop sys.boot_completed 2>$null | Out-String).Trim()
  if ($b -eq '1') { $booted = $true; break }
  if ($proc.HasExited) { break }
  Start-Sleep -Seconds 3
}
$bootSec = [math]::Round($sw.Elapsed.TotalSeconds, 1)
Write-Log "boot_completed=$booted in ${bootSec}s pid=$($proc.Id)"

if (-not $booted) {
  Write-Log 'boot failed -> teardown'
  & $adb emu kill 2>&1 | Out-Null
  Start-Sleep -Seconds 6
  foreach ($p in Get-Group) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
  "BOOT FAILED after ${bootSec}s"
  exit 1
}

Start-Sleep -Seconds 5
& $adb shell screencap -p /sdcard/avd_check.png 2>&1 | Out-Null
& $adb pull /sdcard/avd_check.png $shot 2>&1 | Out-Null
$black = Get-NonBlackRatio $shot
Write-Log "screenshot $shot size=$($black.size) nonBlackRatio=$($black.ratio)"
if ($black.ratio -lt 0.02) {
  Write-Log 'BLACK SCREEN detected -> teardown'
  & $adb emu kill 2>&1 | Out-Null
  Start-Sleep -Seconds 6
  foreach ($p in Get-Group) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
  "BLACK SCREEN: nonBlackRatio=$($black.ratio) -> killed"
  exit 1
}
"START OK boot=${bootSec}s screenshotNonBlack=$($black.ratio) watchdog=$(Test-Watchdog)"
