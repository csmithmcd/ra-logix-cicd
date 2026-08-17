<#
.SYNOPSIS
    Jenkins wrapper for logix_code_compare.py -- requires an interactive desktop.

    Writes a request to C:\data\ld-runner\request.json (generic script_args format),
    triggers the "LD-SDK-Run" Windows Scheduled Task, then polls for
    C:\data\ld-runner\result.exitcode and exits with that code.

.PARAMETER PythonExe
    Absolute path to the venv Python executable.

.PARAMETER ScriptPath
    Absolute path to logix_code_compare.py.

.PARAMETER Repository
    Absolute path to the Git repository root.

.PARAMETER ProjectPath
    Repository-relative ACD/L5X path using forward slashes
    (e.g. 1-production-files/ACDs/ExampleForCICD_L85E.ACD).

.PARAMETER BaseRevision
    Older Git revision to compare against. Default: HEAD^.

.PARAMETER CurrentRevision
    Newer Git revision. Default: HEAD.

.PARAMETER OutputJson
    Absolute path for the JSON comparison artifact.

.PARAMETER OutputMarkdown
    Absolute path for the Markdown comparison artifact.

.PARAMETER TimeoutSeconds
    SDK operation timeout passed to logix_code_compare.py. Default: 600.

.PARAMETER TaskName
    Name of the registered Windows Scheduled Task. Default: LD-SDK-Run.

.EXAMPLE
    powershell.exe -NoProfile -ExecutionPolicy Bypass `
        -File invoke_ld_sdk_compare_interactive.ps1 `
        -PythonExe    "C:\..\.venv-logix-designer\Scripts\python.exe" `
        -ScriptPath   "C:\...\python\compare\logix_code_compare.py" `
        -Repository   "C:\...\ra-logix-cicd" `
        -ProjectPath  "1-production-files/ACDs/ExampleForCICD_L85E.ACD" `
        -OutputJson   "C:\...\python\artifacts\logix-code-compare.json" `
        -OutputMarkdown "C:\...\python\artifacts\logix-code-compare.md"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$PythonExe,
    [Parameter(Mandatory)][string]$ScriptPath,
    [Parameter(Mandatory)][string]$Repository,
    [Parameter(Mandatory)][string]$ProjectPath,
    [string]$BaseRevision      = "HEAD^",
    [string]$CurrentRevision   = "HEAD",
    [Parameter(Mandatory)][string]$OutputJson,
    [Parameter(Mandatory)][string]$OutputMarkdown,
    [int]$TimeoutSeconds       = 600,
    [string]$TaskName          = "LD-SDK-Run"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RunnerDir    = "C:\data\ld-runner"
$RequestFile  = Join-Path $RunnerDir "request.json"
$ExitCodeFile = Join-Path $RunnerDir "result.exitcode"

function Write-Step { param([string]$Msg) Write-Host "[invoke-ld-sdk-compare] $Msg" }

try {
    # Validate required inputs
    foreach ($path in @($PythonExe, $ScriptPath, $Repository)) {
        if (-not (Test-Path -LiteralPath $path)) {
            throw "Required path not found: $path"
        }
    }

    # Build script_args as a JSON array (generic format for ld_sdk_task_action.ps1)
    $scriptArgs = @(
        "--repository",       $Repository,
        "--project-path",     $ProjectPath,
        "--base-revision",    $BaseRevision,
        "--current-revision", $CurrentRevision,
        "--output-json",      $OutputJson,
        "--output-markdown",  $OutputMarkdown,
        "--timeout-seconds",  [string]$TimeoutSeconds
    )

    $request = [ordered]@{
        python_exe      = $PythonExe
        script_path     = $ScriptPath
        script_args     = $scriptArgs
        timeout_seconds = $TimeoutSeconds
        requested_at    = (Get-Date -Format "o")
    }
    $request | ConvertTo-Json | Set-Content -LiteralPath $RequestFile -Encoding UTF8
    Write-Step "Request written to $RequestFile"

    # Clear previous result
    if (Test-Path -LiteralPath $ExitCodeFile) {
        Remove-Item -LiteralPath $ExitCodeFile -Force
    }

    # Verify the task exists
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        throw "Scheduled task '$TaskName' not found. Run setup_ld_sdk_task.ps1 first."
    }

    # Warn if no active interactive session
    $sessions = query session 2>&1 | Select-String "Active"
    if (-not $sessions) {
        Write-Warning "No active interactive session detected. The task may time out."
    } else {
        Write-Step "Active session(s) found: $($sessions -join '; ')"
    }

    # Trigger the task
    Write-Step "Triggering scheduled task '$TaskName'..."
    $runOutput = schtasks.exe /run /tn $TaskName 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "schtasks /run failed (exit $LASTEXITCODE): $runOutput"
    }
    Write-Step "Task triggered."

    # Poll for result.exitcode
    $pollDeadline = (Get-Date).AddSeconds($TimeoutSeconds + 120)
    $pollInterval = 5
    Write-Step "Polling for result (deadline in $($TimeoutSeconds + 120)s)..."

    while ((Get-Date) -lt $pollDeadline) {
        Start-Sleep -Seconds $pollInterval
        if (Test-Path -LiteralPath $ExitCodeFile) {
            $raw = (Get-Content -LiteralPath $ExitCodeFile -Raw).Trim()
            $exitCode = [int]$raw
            Write-Step "Task completed - exit code: $exitCode"
            exit $exitCode
        }
        if ($pollInterval -lt 15) { $pollInterval++ }
    }

    throw "Timed out waiting for '$TaskName' to write result.exitcode after $($TimeoutSeconds + 120)s."
}
catch {
    [Console]::Error.WriteLine("[invoke-ld-sdk-compare] ERROR: $($_.Exception.Message)")
    exit 2
}
