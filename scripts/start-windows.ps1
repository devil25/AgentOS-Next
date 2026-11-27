$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$frontendDir = Join-Path $repoRoot 'frontend'
. (Join-Path $PSScriptRoot 'node-prerequisites.ps1')
$runtimeConfigPath = Join-Path $frontendDir 'config\runtime.json'
$viteConfigPath = Join-Path $frontendDir 'vite.config.ts'
$monawHome = if ($env:MONAW_HOME) {
  $env:MONAW_HOME
} elseif ($env:AGENT_HOME) {
  $env:AGENT_HOME
} else {
  Join-Path $HOME '.monaw'
}
$runtimeDir = if ($env:AGENT_RUNTIME_DIR) {
  $env:AGENT_RUNTIME_DIR
} else {
  Join-Path $monawHome 'runtime'
}
$launcherLogDir = Join-Path $runtimeDir 'launcher'

New-Item -ItemType Directory -Force -Path $launcherLogDir | Out-Null

# A PowerShell process normally inherits the merged Machine/User PATH.
# Re-add only missing entries, preserving the current process order and
# avoiding duplicate PATH growth across nested/repeated launcher sessions.
$machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$pathEntries = [System.Collections.Generic.List[string]]::new()
$seenPathEntries = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
$pathLength = 0
$maxPathLength = 32767

foreach ($pathSource in @($env:Path, $machinePath, $userPath)) {
  foreach ($entry in ([string]$pathSource -split ';')) {
    $candidate = $entry.Trim()
    if (-not $candidate) { continue }
    $candidate = $candidate.TrimEnd([char]'/','\')
    if (-not $seenPathEntries.Add($candidate)) { continue }

    $additionalLength = $candidate.Length
    if ($pathEntries.Count -gt 0) { $additionalLength++ }
    if (($pathLength + $additionalLength) -gt $maxPathLength) {
      Write-Warning "Skipped a PATH entry because the Windows environment-variable length limit would be exceeded: $candidate"
      continue
    }

    $pathEntries.Add($candidate)
    $pathLength += $additionalLength
  }
}

$mergedPath = $pathEntries -join ';'
if ($env:Path -ne $mergedPath) {
  $env:Path = $mergedPath
}

$null = Assert-MonawNodeRuntime
