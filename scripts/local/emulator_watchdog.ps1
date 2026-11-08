param(
  [double]$LimitPercent = 30.0,
  [int]$GraceSeconds = 120,
  [int]$IntervalSeconds = 2,
  [string]$LogPath = 'D:\android-avd\watchdog.log',
  [switch]$DryRun
)
$ErrorActionPreference = 'Continue'
$stopFile = 'D:\android-avd\watchdog.stop'
$pidFile = 'D:\android-avd\watchdog.pid'
$cores = (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors

if ($DryRun) {
  "LIMIT=on threshold=$LimitPercent% grace=${GraceSeconds}s interval=${IntervalSeconds}s cores=$cores start_window=21:00-06:00 log=$LogPath"
  exit 0
}

Remove-Item -LiteralPath $stopFile -Force -ErrorAction SilentlyContinue
$PID | Set-Content -LiteralPath $pidFile -Encoding UTF8

function Write-Log([string]$msg) {
  "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff') $msg" | Add-Content -LiteralPath $LogPath -Encoding UTF8
}

function Get-EmuGroup {
  $all = Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('emulator.exe','qemu-system-x86_64.exe') }
  if (-not $all) { return @() }
  $ids = @($all.ProcessId)
  $extra = Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -in $ids -and $_.Name -in @('crashpad_handler.exe','netsimd.exe','adb.exe') }
  return @($all) + @($extra)
}

# 策略（2026-11-08 定稿）：30% 硬线始终生效，不再有 21:00-06:00 免限窗口。
# 启动时段限制由 avd_local.ps1 负责；本看门狗只负责按阈值击杀。
$limitLabel = 'LIMIT=on'
$mono = [Diagnostics.Stopwatch]::StartNew()
$lastMono = 0.0

Write-Log "watchdog start limit=$LimitPercent% grace=${GraceSeconds}s interval=${IntervalSeconds}s cores=$cores state=$limitLabel start_window=21:00-06:00"
$prev = @{}
$peak = 0.0
$groupStart = $null

while (-not (Test-Path -LiteralPath $stopFile)) {
  Start-Sleep -Seconds $IntervalSeconds
  $now = Get-Date
  $m = $mono.Elapsed.TotalSeconds
  $dt = $m - $lastMono
  $lastMono = $m
  if ($dt -le 0) { $dt = $IntervalSeconds }
  $group = Get-EmuGroup
  if ($group.Count -eq 0) {
    $prev = @{}
    $peak = 0.0
    $groupStart = $null
    continue
  }

  # earliest creation time defines the launch (grace window)
  $starts = $group | ForEach-Object { [datetime]$_.CreationDate }
  $first = ($starts | Sort-Object | Select-Object -First 1)
  if ($null -eq $groupStart) { $groupStart = $first; $peak = 0.0 }

  $sum = 0.0
  $per = @()
  foreach ($p in $group) {
    $gp = Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
    if ($gp) {
      $cur = $gp.TotalProcessorTime.TotalSeconds
      if ($prev.ContainsKey($p.ProcessId)) {
        $c = (($cur - $prev[$p.ProcessId]) / $dt / $cores) * 100
        if ($c -lt 0) { $c = 0 }
        $sum += $c
        $per += "$($p.Name)#$($p.ProcessId)=$([math]::Round($c,1))%"
      }
      $prev[$p.ProcessId] = $cur
    } else {
      $prev.Remove($p.ProcessId)
    }
  }

  $total = [math]::Round($sum, 1)
  if ($total -gt $peak) { $peak = $total }
  $age = [math]::Round(($now - $groupStart).TotalSeconds, 1)
  $exempt = ($age -le $GraceSeconds)

  if ($total -gt $LimitPercent -and -not $exempt) {
    $focus = ''
    try {
      $adb = 'D:\android-sdk\platform-tools\adb.exe'
      $focus = (& $adb shell dumpsys window 2>$null | Select-String 'mCurrentFocus' | Select-Object -First 1)
      if ($focus) { $focus = $focus.Line.Trim() } else { $focus = 'n/a' }
    } catch { $focus = 'adb-error' }
    $duration = [math]::Round(($now - $groupStart).TotalSeconds, 1)

    Write-Log "KILL trigger total=$total% > limit=$LimitPercent% age=${age}s exempt=$exempt peak=$peak% per=[$($per -join ', ')] focus=[$focus]"
    foreach ($p in $group) {
      try {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
        Write-Log "  killed pid=$($p.ProcessId) name=$($p.Name) created=$(([datetime]$p.CreationDate).ToString('HH:mm:ss'))"
      } catch {
        Write-Log "  kill-fail pid=$($p.ProcessId) name=$($p.Name) err=$($_.Exception.Message)"
      }
    }
    Start-Sleep -Seconds 3
    $left = Get-EmuGroup
    if ($left.Count -gt 0) {
      foreach ($p in $left) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    }
    Write-Log "SUMMARY killed emulator group: ran=${duration}s peak_cpu=$peak% threshold=$LimitPercent% residual=$((Get-EmuGroup).Count)"
    $prev = @{}
    $peak = 0.0
    $groupStart = $null
  }
}
Write-Log 'watchdog stop (flag file detected)'
Remove-Item -LiteralPath $stopFile -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
