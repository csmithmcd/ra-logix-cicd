<#
.SYNOPSIS
    Logix Designer SDK scheduled-task action.
    Runs inside the interactive desktop session (logix-runner or whoever is
    currently logged on). Reads C:\data\ld-runner\request.json, executes the
    specified Python smoke script, writes the JSON artifact to the path in the
    request, and writes the integer exit code to C:\data\ld-runner\result.exitcode.

.NOTES
    This script is invoked by the Windows Scheduled Task "LD-SDK-Run".
    Do NOT call it directly from Jenkins - use invoke_ld_sdk_interactive.ps1.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RunnerDir    = "C:\data\ld-runner"
$RequestFile  = Join-Path $RunnerDir "request.json"
$ExitCodeFile = Join-Path $RunnerDir "result.exitcode"

$exitCode = 2  # CONFIGURATION_ERROR default

try {
    if (-not (Test-Path -LiteralPath $RequestFile -PathType Leaf)) {
        throw "Request file not found: $RequestFile"
    }

    $req = Get-Content -LiteralPath $RequestFile -Raw | ConvertFrom-Json

    $pythonExe  = $req.python_exe
    $scriptPath = $req.script_path
    $acdPath    = $req.acd_path
    $outputPath = $req.output_path
    $modeFlag   = $req.mode_flag
    $timeoutSec = [int]$req.timeout_seconds

    Write-Host "LD-SDK task action starting"
    Write-Host "  Python  : $pythonExe"
    Write-Host "  Script  : $scriptPath"
    Write-Host "  ACD     : $acdPath"
    Write-Host "  Output  : $outputPath"
    Write-Host "  Mode    : $(if ($modeFlag) { $modeFlag } else { '(open/close only)' })"
    Write-Host "  Timeout : ${timeoutSec}s"
    Write-Host "  Identity: $([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)"

    $arguments = @(
        $scriptPath,
        "--project",         $acdPath,
        "--output",          $outputPath,
        "--timeout-seconds", $timeoutSec
    )
    if (-not [string]::IsNullOrWhiteSpace($modeFlag)) {
        $arguments += $modeFlag
    }

    & $pythonExe @arguments
    $exitCode = $LASTEXITCODE

    Write-Host "Python exited with code: $exitCode"
}
catch {
    [Console]::Error.WriteLine("ld_sdk_task_action.ps1 error: $($_.Exception.Message)")
    $exitCode = 2
}
finally {
    Set-Content -LiteralPath $ExitCodeFile -Value $exitCode -Encoding ASCII
    Write-Host "Exit code written to: $ExitCodeFile"
}

exit $exitCode
