<#
.SYNOPSIS
    Logix Designer SDK scheduled-task action.
    Runs inside the interactive desktop session (logix-runner or whoever is
    currently logged on). Reads C:\data\ld-runner\request.json, executes the
    specified Python script, and writes the integer exit code to
    C:\data\ld-runner\result.exitcode.

    Two request formats are supported:

    Format A - generic (preferred for new callers):
        { "python_exe": "...", "script_path": "...", "script_args": [...],
          "timeout_seconds": 600 }
        script_args is a JSON array of strings appended verbatim after script_path.

    Format B - legacy (logix_designer_executables.py callers):
        { "python_exe": "...", "script_path": "...",
          "acd_path": "...", "output_path": "...", "mode_flag": "...",
          "timeout_seconds": 600 }
        script_args is absent; arguments are constructed from named fields.

.NOTES
    Invoked by the Windows Scheduled Task "LD-SDK-Run".
    Do NOT call directly from Jenkins -- use an invoke_ld_sdk_*_interactive.ps1 wrapper.
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
    $timeoutSec = [int]$req.timeout_seconds

    Write-Host "LD-SDK task action starting"
    Write-Host "  Python   : $pythonExe"
    Write-Host "  Script   : $scriptPath"
    Write-Host "  Timeout  : ${timeoutSec}s"
    Write-Host "  Identity : $([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)"

    # Resolve argument list: generic script_args array takes precedence over legacy fields
    if ($null -ne $req.PSObject.Properties["script_args"] -and $null -ne $req.script_args) {
        $extraArgs = @($req.script_args)
        Write-Host "  Format   : generic (script_args)"
        Write-Host "  Args     : $($extraArgs -join ' ')"
        $arguments = @($scriptPath) + $extraArgs
    } else {
        # Legacy format: build args for logix_designer_executables.py
        $acdPath    = $req.acd_path
        $outputPath = $req.output_path
        $modeFlag   = $req.mode_flag
        Write-Host "  Format   : legacy"
        Write-Host "  ACD      : $acdPath"
        Write-Host "  Output   : $outputPath"
        Write-Host "  Mode     : $(if ($modeFlag) { $modeFlag } else { '(open/close only)' })"
        $arguments = @(
            $scriptPath,
            "--project",         $acdPath,
            "--output",          $outputPath,
            "--timeout-seconds", $timeoutSec
        )
        if (-not [string]::IsNullOrWhiteSpace($modeFlag)) {
            $arguments += $modeFlag
        }
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
