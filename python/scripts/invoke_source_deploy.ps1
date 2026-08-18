<#
.SYNOPSIS
    Cross-project entry point for the Rockwell CI source-to-Echo pipeline.

    Resolves all tool paths automatically, creates (or reuses) machine-level
    Python virtual environments under C:\data\rockwell-ci\, then runs the
    three-step pipeline for the given source directory:

        1. l5xplode implode   Source/ -> build/Controller.L5X
        2. l5x_to_acd.py     Controller.L5X -> build/Controller.ACD  (LD-SDK-Run)
        3. echo_deploy.py    Controller.ACD -> Echo (download, verify, delete)

    Only -SourceDir is required. Everything else has sensible defaults.

    The venvs and SDK wheels are expected at stable machine paths and are
    created once on first use. Subsequent calls reuse them (skip-if-exists).
    The venvs are NOT tied to any Jenkins workspace or specific git repository.

.PARAMETER SourceDir
    Root of the exploded source tree -- must contain RSLogix5000Content/.
    Typically the Source/ folder of the target Logix project repository.
    Example: C:\Projects\my-plc-project\Source

.PARAMETER BuildDir
    Directory for intermediate artifacts (Controller.L5X, Controller.ACD,
    l5x-to-acd.json). Created if absent.
    Default: rockwell-ci-build\ sibling to SourceDir.

.PARAMETER Output
    JSON pipeline summary artifact path.
    Default: source-deploy.json inside BuildDir.

.PARAMETER TimeoutSeconds
    Per-step timeout in seconds. Passed to source_deploy.py. Default: 300.

.PARAMETER TaskName
    Name of the LD-SDK-Run Windows Scheduled Task. Default: LD-SDK-Run.

.PARAMETER PythonExe
    Python 3.13 x64 executable used to create the venvs.
    Default: C:\Program Files\Python313\python.exe.

.EXAMPLE
    # Minimal -- deploys the local Source/ folder to Echo:
    .\invoke_source_deploy.ps1 -SourceDir "C:\Projects\my-plc\Source"

.EXAMPLE
    # From another project's Jenkins bat step:
    powershell.exe -NoProfile -ExecutionPolicy Bypass ^
        -File "C:\Projects\ra-logix-cicd\python\scripts\invoke_source_deploy.ps1" ^
        -SourceDir "%WORKSPACE%\Source" ^
        -Output    "%WORKSPACE%\source-deploy.json"

.NOTES
    Machine-level paths used by this script (all must exist before first run):

      Venvs (created on first use):
        C:\data\rockwell-ci\venv-echo\
        C:\data\rockwell-ci\venv-logix-designer\

      SDK wheels (pre-installed with the Rockwell software):
        FactoryTalk Logix Echo SDK   -- Public Documents
        Logix Designer SDK           -- Public Documents

      l5xplode binary (built once from ra-logix-designer-vcs-custom-tools):
        C:\Projects\ra-logix-designer-vcs-custom-tools\artifacts\bin\Release\

      Tool scripts (in this repo, resolved relative to this script):
        python\build\l5x_to_acd.py
        python\deploy\source_deploy.py
        python\deploy\echo_deploy.py
        python\scripts\setup_venv.ps1
        python\scripts\setup_logix_designer_venv.ps1
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$SourceDir,
    [string]$BuildDir       = "",
    [string]$Output         = "",
    [int]$TimeoutSeconds    = 300,
    [string]$TaskName       = "LD-SDK-Run",
    [string]$PythonExe      = "C:\Program Files\Python313\python.exe"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step { param([string]$Msg) Write-Host "[invoke-source-deploy] $Msg" }

# ---------------------------------------------------------------------------
# Resolve tool root from this script's own location.
#   This script lives at: <ToolRoot>\python\scripts\invoke_source_deploy.ps1
#   So ToolRoot = grandparent of $PSScriptRoot.
# ---------------------------------------------------------------------------
$ToolRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

# ---------------------------------------------------------------------------
# Machine-level stable paths (do NOT change without updating machine setup).
# ---------------------------------------------------------------------------
$RockwellCiDir = "C:\data\rockwell-ci"
$VenvEcho      = Join-Path $RockwellCiDir "venv-echo"
$VenvLd        = Join-Path $RockwellCiDir "venv-logix-designer"

$L5xplodeExe   = "C:\Projects\ra-logix-designer-vcs-custom-tools\artifacts\bin\Release\l5xplode.exe"
$EchoSdkWheel  = "C:\Users\Public\Documents\FactoryTalk Logix Echo\SDK\Python\ftecho_sdk-4.0.0-py3-none-any.whl"
$LdSdkWheel    = "C:\Users\Public\Documents\Studio 5000\Logix Designer SDK\python\logix_designer_sdk-2.0.2-py3-none-any.whl"

# ---------------------------------------------------------------------------
# Tool script paths (all relative to ToolRoot).
# ---------------------------------------------------------------------------
$SourceDeployScript = Join-Path $ToolRoot "python\deploy\source_deploy.py"
$L5xToAcdScript     = Join-Path $ToolRoot "python\build\l5x_to_acd.py"
$EchoDeployScript   = Join-Path $ToolRoot "python\deploy\echo_deploy.py"
$SetupVenvScript    = Join-Path $ToolRoot "python\scripts\setup_venv.ps1"
$SetupLdVenvScript  = Join-Path $ToolRoot "python\scripts\setup_logix_designer_venv.ps1"

# ---------------------------------------------------------------------------
# Default BuildDir and Output.
# ---------------------------------------------------------------------------
if ([string]::IsNullOrWhiteSpace($BuildDir)) {
    $SourceResolved = (Resolve-Path $SourceDir).Path
    $BuildDir = Join-Path (Split-Path $SourceResolved -Parent) "rockwell-ci-build"
}
if ([string]::IsNullOrWhiteSpace($Output)) {
    $Output = Join-Path $BuildDir "source-deploy.json"
}

# ---------------------------------------------------------------------------
# Validate prerequisites (fail fast before touching venvs).
# ---------------------------------------------------------------------------
Write-Step "Validating prerequisites..."
$errors = @()
if (-not (Test-Path -LiteralPath $SourceDir -PathType Container)) {
    $errors += "SourceDir not found: $SourceDir"
}
foreach ($p in @($L5xplodeExe, $EchoSdkWheel, $LdSdkWheel,
                 $SourceDeployScript, $L5xToAcdScript, $EchoDeployScript,
                 $SetupVenvScript, $SetupLdVenvScript)) {
    if (-not (Test-Path -LiteralPath $p)) {
        $errors += "Required file not found: $p"
    }
}
if ($errors.Count -gt 0) {
    foreach ($e in $errors) { [Console]::Error.WriteLine("[invoke-source-deploy] ERROR: $e") }
    exit 2
}
New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null

Write-Step "ToolRoot  : $ToolRoot"
Write-Step "SourceDir : $SourceDir"
Write-Step "BuildDir  : $BuildDir"
Write-Step "Output    : $Output"

# ---------------------------------------------------------------------------
# Ensure Echo venv (idempotent -- setup script skips if already valid).
# ---------------------------------------------------------------------------
Write-Step "Ensuring Echo venv at $VenvEcho ..."
& $SetupVenvScript `
    -PythonExecutable $PythonExe `
    -EchoSdkWheel     $EchoSdkWheel `
    -VenvPath         $VenvEcho
if ($LASTEXITCODE -ne 0) {
    [Console]::Error.WriteLine("[invoke-source-deploy] ERROR: Echo venv setup failed (exit $LASTEXITCODE)")
    exit 2
}

# ---------------------------------------------------------------------------
# Ensure Logix Designer venv (idempotent).
# ---------------------------------------------------------------------------
Write-Step "Ensuring Logix Designer venv at $VenvLd ..."
& $SetupLdVenvScript `
    -PythonExecutable      $PythonExe `
    -LogixDesignerSdkWheel $LdSdkWheel `
    -VenvPath              $VenvLd
if ($LASTEXITCODE -ne 0) {
    [Console]::Error.WriteLine("[invoke-source-deploy] ERROR: Logix Designer venv setup failed (exit $LASTEXITCODE)")
    exit 2
}

# ---------------------------------------------------------------------------
# Run source_deploy.py.
# ---------------------------------------------------------------------------
$VenvPython = Join-Path $VenvEcho "Scripts\python.exe"
$LdPython   = Join-Path $VenvLd   "Scripts\python.exe"

Write-Step "Running source_deploy.py ..."
& $VenvPython $SourceDeployScript `
    --source-dir         $SourceDir `
    --build-dir          $BuildDir `
    --l5xplode           $L5xplodeExe `
    --ld-python-exe      $LdPython `
    --l5x-to-acd-script  $L5xToAcdScript `
    --echo-deploy-script $EchoDeployScript `
    --output             $Output `
    --timeout-seconds    $TimeoutSeconds `
    --task-name          $TaskName

exit $LASTEXITCODE
