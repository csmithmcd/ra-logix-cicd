<#
.SYNOPSIS
    One-time setup: registers the "LD-SDK-Run" Windows Scheduled Task.

    The task runs C:\data\ld-runner\ld_sdk_task_action.ps1 inside the active
    interactive desktop session (INTERACTIVE group principal — whoever is
    currently logged on). No password is stored.

    Run this once on NIA-AUTO-ECH-01 as Administrator.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$TaskName   = "LD-SDK-Run"
$ActionScript = "C:\data\ld-runner\ld_sdk_task_action.ps1"
$RunnerDir  = "C:\data\ld-runner"

# Copy the task action script to the runner directory (it must be at a stable
# path that doesn't change with each git checkout)
$sourceAction = Join-Path $PSScriptRoot "ld_sdk_task_action.ps1"
Copy-Item -LiteralPath $sourceAction -Destination $ActionScript -Force
Write-Host "Task action script copied to: $ActionScript"

# Build scheduled task components
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$ActionScript`"" `
    -WorkingDirectory $RunnerDir

# INTERACTIVE group: task runs in the current active interactive session,
# as whoever is logged on. No password stored.
$principal = New-ScheduledTaskPrincipal `
    -GroupId "INTERACTIVE" `
    -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances  IgnoreNew

# Register (or overwrite) the task
Register-ScheduledTask `
    -TaskName    $TaskName `
    -Action      $action `
    -Principal   $principal `
    -Settings    $settings `
    -Description "Runs Logix Designer SDK operations in the active interactive session for Jenkins CI." `
    -Force | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName
Write-Host "Registered task '$TaskName'"
Write-Host "  State    : $($task.State)"
Write-Host "  Principal: $($task.Principal.GroupId)"
Write-Host "  Run level: $($task.Principal.RunLevel)"
Write-Host ""
Write-Host "Setup complete. Jenkins calls invoke_ld_sdk_interactive.ps1 to trigger this task."
