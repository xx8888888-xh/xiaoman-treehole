param(
  [ValidateSet('start','stop','status')]
  [string]$Action = 'status',
  [string]$Avd = 'treehole_api34',
  [string]$Gpu = 'swiftshader_indirect',
  [int]$Cores = 4,
  [int]$MemoryMb = 3072,
  [int]$BootTimeoutSec = 240,
  [switch]$StopWatchdog
)
$ErrorActionPreference = 'Continue'
$env:ANDROID_HOME = 'D:\android-sdk'
$env:ANDROID_SDK_ROOT = 'D:\android-sdk'
$env:ANDROID_AVD_HOME = 'D:\android-avd'
$env:ANDROID_USER_HOME = 'D:\android-avd\user-home'
$env:ANDROID_EMULATOR_HOME = 'D:\android-avd\user-home'
$emu = 'D:\android-sdk\emulator\emulator.exe'
$adb = 'D:\android-sdk\platform-tools\adb.exe'
$watchdog = 'D:\android-avd\emulator_watchdog.ps1'
$log = 'D:\android-avd\avd_local.log'
$shot = 'D:\android-avd\avd_last.png'
$coresTotal = (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors

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
  Start-Sleep -Seconds 2
  Write-Log "watchdog started: $(Test-Watchdog)"
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

if ($Action -eq 'status') {
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
Start-Watchdog
$left = Get-Group
if ($left) {
  foreach ($p in $left) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
  Start-Sleep -Seconds 4
}
& $adb start-server 2>&1 | Out-Null

$args = @('-avd', $Avd, '-no-audio', '-no-boot-anim', '-gpu', $Gpu, '-no-metrics', '-cores', "$Cores", '-memory', "$MemoryMb")
Write-Log "launch avd=$Avd gpu=$Gpu cores=$Cores mem=$MemoryMb"
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
