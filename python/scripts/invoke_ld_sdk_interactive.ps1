<#
.SYNOPSIS
    Jenkins wrapper for Logix Designer SDK operations that require an
    interactive desktop session.

    Writes a request to C:\data\ld-runner\request.json, triggers the
    "LD-SDK-Run" Windows Scheduled Task (which runs in the active interactive
    session as logix-runner or whoever is currently logged on), then polls
    for C:\data\ld-runner\result.exitcode and exits with that code.

    The scheduled task writes the JSON artifact directly to -OutputPath.

.PARAMETER PythonExe
    Absolute path to the venv Python executable.

.PARAMETER ScriptPath
    Absolute path to logix_designer_executables.py.

.PARAMETER AcdPath
    Absolute path to the source .ACD file.

.PARAMETER OutputPath
    Absolute path where the JSON result artifact will be written.

.PARAMETER ModeFlag
    Optional mode flag passed to the script:
    --enumerate-executables | --project-inventory | --build-validation
    Leave empty for open/close-only probe.

.PARAMETER TimeoutSeconds
    Passed to the Python script as --timeout-seconds. The wrapper waits
    this value plus 120 seconds before giving up.

.PARAMETER TaskName
    Name of the registered Windows Scheduled Task. Default: LD-SDK-Run.

.EXAMPLE
    powershell.exe -NoProfile -ExecutionPolicy Bypass `
        -File invoke_ld_sdk_interactive.ps1 `
        -PythonExe "C:\..\.venv-logix-designer\Scripts\python.exe" `
        -ScriptPath "C:\...\logix_designer_executables.py" `
        -AcdPath "C:\...\ExampleForCICD_L85E.ACD" `
        -OutputPath "C:\...\logix-designer-open-project.json"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$PythonExe,
    [Parameter(Mandatory)][string]$ScriptPath,
    [Parameter(Mandatory)][string]$AcdPath,
    [Parameter(Mandatory)][string]$OutputPath,
    [string]$ModeFlag       = "",
    [int]$TimeoutSeconds    = 600,
    [string]$TaskName       = "LD-SDK-Run"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RunnerDir    = "C:\data\ld-runner"
$RequestFile  = Join-Path $RunnerDir "request.json"
$ExitCodeFile = Join-Path $RunnerDir "result.exitcode"

function Write-Step { param([string]$Msg) Write-Host "[invoke-ld-sdk] $Msg" }

try {
    # --- Validate inputs ---
    foreach ($path in @($PythonExe, $ScriptPath, $AcdPath)) {
        if (-not (Test-Path -LiteralPath $path)) {
            throw "Required path not found: $path"
        }
    }

    # --- Write request ---
    $request = [ordered]@{
        python_exe      = $PythonExe
        script_path     = $ScriptPath
        acd_path        = $AcdPath
        output_path     = $OutputPath
        mode_flag       = $ModeFlag
        timeout_seconds = $TimeoutSeconds
        requested_at    = (Get-Date -Format "o")
    }
    $request | ConvertTo-Json | Set-Content -LiteralPath $RequestFile -Encoding UTF8
    Write-Step "Request written to $RequestFile"

    # --- Clear previous result ---
    if (Test-Path -LiteralPath $ExitCodeFile) {
        Remove-Item -LiteralPath $ExitCodeFile -Force
    }

    # --- Check task exists ---
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        throw "Scheduled task '$TaskName' not found. Run setup_ld_sdk_task.ps1 first."
    }

    # --- Check an interactive session is available ---
    $sessions = query session 2>&1 | Select-String "Active"
    if (-not $sessions) {
        Write-Warning "No active interactive session detected. The scheduled task may run in session 0 and time out."
    } else {
        Write-Step "Active session(s) found: $($sessions -join '; ')"
    }

    # --- Trigger the task ---
    Write-Step "Triggering scheduled task '$TaskName'..."
    $runOutput = schtasks.exe /run /tn $TaskName 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "schtasks /run failed (exit $LASTEXITCODE): $runOutput"
    }
    Write-Step "Task triggered successfully."

    # --- Poll for result.exitcode ---
    $pollDeadline = (Get-Date).AddSeconds($TimeoutSeconds + 120)
    $pollInterval = 5
    Write-Step "Polling for result (deadline in $($TimeoutSeconds + 120)s)..."

    while ((Get-Date) -lt $pollDeadline) {
        Start-Sleep -Seconds $pollInterval
        if (Test-Path -LiteralPath $ExitCodeFile) {
            $raw = (Get-Content -LiteralPath $ExitCodeFile -Raw).Trim()
            $exitCode = [int]$raw
            Write-Step "Task completed — exit code: $exitCode"
            exit $exitCode
        }
        # Increase poll interval after 30s to reduce noise
        if ($pollInterval -lt 15) { $pollInterval++ }
    }

    throw "Timed out waiting for '$TaskName' to write result.exitcode after $($TimeoutSeconds + 120)s."
}
catch {
    [Console]::Error.WriteLine("[invoke-ld-sdk] ERROR: $($_.Exception.Message)")
    exit 2
}
