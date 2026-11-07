param(
  [double]$LimitPercent = 30.0,
  [int]$GraceSeconds = 120,
  [int]$IntervalSeconds = 2,
  [string]$LogPath = 'D:\android-avd\watchdog.log'
)
$ErrorActionPreference = 'Continue'
$stopFile = 'D:\android-avd\watchdog.stop'
$pidFile = 'D:\android-avd\watchdog.pid'
Remove-Item -LiteralPath $stopFile -Force -ErrorAction SilentlyContinue
$PID | Set-Content -LiteralPath $pidFile -Encoding UTF8
$cores = (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors

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

$timeMod = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'emu_timewindow.ps1'
if (-not (Test-Path -LiteralPath $timeMod)) { $timeMod = 'D:\android-avd\emu_timewindow.ps1' }
. $timeMod

# 时间窗状态：21:00-06:00 LIMIT=off（不采样不杀），其余 LIMIT=on
$limitOff = Test-QuietWindow (Get-Date)
$mono = [Diagnostics.Stopwatch]::StartNew()
$lastWall = Get-Date
$lastMono = 0.0
$candidate = $null
$candidateCount = 0

Write-Log "watchdog start limit=$LimitPercent% grace=${GraceSeconds}s interval=${IntervalSeconds}s cores=$cores window=21:00-06:00 state=$(Get-LimitLabel (Get-Date))"
$prev = @{}
$lastTick = Get-Date
$peak = 0.0
$groupStart = $null

while (-not (Test-Path -LiteralPath $stopFile)) {
  Start-Sleep -Seconds $IntervalSeconds
  $now = Get-Date
  $m = $mono.Elapsed.TotalSeconds
  $wallDelta = ($now - $lastWall).TotalSeconds
  $monoDelta = $m - $lastMono
  $lastWall = $now
  $lastMono = $m
  if ($wallDelta -le 0) { $wallDelta = $IntervalSeconds }
  $dt = $wallDelta
  $lastTick = $now

  # 时钟跳变：墙钟增量与单调时钟偏差 > 5s 时，本次不信任墙钟，保持原状态并重新确认
  if ([math]::Abs($wallDelta - $monoDelta) -gt 5) {
    Write-Log "clock jump wall_delta=$([math]::Round($wallDelta,1))s mono_delta=$([math]::Round($monoDelta,1))s -> keep $(Get-LimitLabel $now) state, re-confirm next samples"
    $candidate = $null
    $candidateCount = 0
    $prev = @{}
    $lastTick = $now
    continue
  }

  $rawOff = Test-QuietWindow $now
  if ($rawOff -ne $limitOff) {
    if ($candidate -eq $rawOff) { $candidateCount++ } else { $candidate = $rawOff; $candidateCount = 1 }
    # 连续 2 次采样一致才切换，单次跳变不会立刻改判
    if ($candidateCount -ge 2) {
      $limitOff = $rawOff
      $candidate = $null
      $candidateCount = 0
      Write-Log "window switch -> $(Get-LimitLabel $now) (2 consecutive samples confirmed)"
    }
  } else {
    $candidate = $null
    $candidateCount = 0
  }

  if ($limitOff) {
    # 免限时窗内：不采样、不杀
    $prev = @{}
    $peak = 0.0
    $groupStart = $null
    continue
  }

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
