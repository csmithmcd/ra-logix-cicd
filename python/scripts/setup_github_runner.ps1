<#
.SYNOPSIS
    Install and register a GitHub Actions self-hosted runner on NIA-AUTO-ECH-01.

    Run this once. After it completes, GitHub Actions workflows that specify
    runs-on: [self-hosted, NIA-AUTO-ECH-01] will execute on this machine.

.PARAMETER Token
    Registration token from:
    GitHub repo > Settings > Actions > Runners > New self-hosted runner
    Tokens expire after one hour -- generate immediately before running this script.

.PARAMETER RepoUrl
    Full HTTPS URL of the GitHub repository.
    Example: https://github.com/your-org/ra-logix-cicd

.PARAMETER RunnerDir
    Directory to install the runner into. Default: C:\actions-runner

.PARAMETER RunnerName
    Name shown in GitHub's runner list. Default: NIA-AUTO-ECH-01

.EXAMPLE
    .\setup_github_runner.ps1 `
        -Token   "AABBCCDD..." `
        -RepoUrl "https://github.com/your-org/ra-logix-cicd"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Token,
    [Parameter(Mandatory)][string]$RepoUrl,
    [string]$RunnerDir  = "C:\actions-runner",
    [string]$RunnerName = "NIA-AUTO-ECH-01"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step { param([string]$Msg) Write-Host "[setup-runner] $Msg" }

# ---- Resolve latest runner version via GitHub API ----
Write-Step "Looking up latest GitHub Actions runner release..."
$release = Invoke-RestMethod -Uri "https://api.github.com/repos/actions/runner/releases/latest" -UseBasicParsing
$version = $release.tag_name.TrimStart("v")
$zipUrl = "https://github.com/actions/runner/releases/download/v${version}/actions-runner-win-x64-${version}.zip"
Write-Step "Latest runner: v$version"
Write-Step "Download URL : $zipUrl"

# ---- Download ----
Write-Step "Creating runner directory: $RunnerDir"
New-Item -ItemType Directory -Path $RunnerDir -Force | Out-Null

$zipPath = Join-Path $RunnerDir "actions-runner.zip"
Write-Step "Downloading runner package..."
Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing
Write-Step "Download complete. Extracting..."
Expand-Archive -Path $zipPath -DestinationPath $RunnerDir -Force
Remove-Item $zipPath

# ---- Configure ----
Write-Step "Configuring runner (name: $RunnerName, repo: $RepoUrl)..."
Set-Location $RunnerDir
.\config.cmd `
    --url          $RepoUrl `
    --token        $Token `
    --name         $RunnerName `
    --labels       "self-hosted,windows,NIA-AUTO-ECH-01" `
    --work         "_work" `
    --runasservice `
    --windowslogonaccount "LocalSystem" `
    --unattended

if ($LASTEXITCODE -ne 0) {
    Write-Error "config.cmd failed (exit $LASTEXITCODE). Check that the token is valid and has not expired."
    exit 1
}

# --runasservice on config.cmd already installed and started the Windows service.
# svc.cmd is only needed when configuring without --runasservice; remove it here.

Write-Step ""
Write-Step "Runner installed and started."
Write-Step "Verify at: $RepoUrl/settings/actions/runners"
Write-Step ""
Write-Step "The runner service is named 'actions.runner.*'. Check status with:"
Write-Step "  Get-Service 'actions.runner*'"
