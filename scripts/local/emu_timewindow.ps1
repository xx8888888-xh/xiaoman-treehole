# Time window: 21:00-06:00 local time => LIMIT=off (no CPU/GPU cap); otherwise 30% hard limit (LIMIT=on).
# Cross-midnight: 22:30, 00:30, 05:59 are inside; 06:00 and later are outside.
# Shared by avd_local.ps1 and emulator_watchdog.ps1 so both use the same decision.
function Test-QuietWindow([datetime]$t) {
  $h = $t.Hour
  return ($h -ge 21 -or $h -lt 6)
}

function Get-LimitLabel([datetime]$t) {
  if (Test-QuietWindow $t) { return 'LIMIT=off' }
  return 'LIMIT=on'
}
