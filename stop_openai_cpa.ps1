Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptName = "wfxl_openai_regst.py"

$targets = Get-CimInstance Win32_Process |
    Where-Object {
        $cmd = [string]($_.CommandLine)
        $exe = [string]($_.ExecutablePath)
        $cmd -like "*$scriptName*" -and (
            $cmd -like "*$repoPath*" -or
            $exe -like "*$repoPath*"
        )
    }

if (-not $targets) {
    Write-Host "[openai-cpa] No running project process found."
    exit 0
}

foreach ($proc in $targets) {
    $pidValue = [int]$proc.ProcessId
    $name = [string]$proc.Name
    Write-Host ("[openai-cpa] Stopping PID={0} ({1})" -f $pidValue, $name)
    Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 1

$remaining = Get-CimInstance Win32_Process |
    Where-Object {
        $cmd = [string]($_.CommandLine)
        $exe = [string]($_.ExecutablePath)
        $cmd -like "*$scriptName*" -and (
            $cmd -like "*$repoPath*" -or
            $exe -like "*$repoPath*"
        )
    }

if ($remaining) {
    Write-Host "[openai-cpa] Some processes are still running. Please check manually."
    exit 1
}

Write-Host "[openai-cpa] Project processes stopped."
