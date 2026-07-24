[CmdletBinding()]
param(
    [string]$PythonExecutable = "",
    [string]$LogixDesignerSdkWheel = "",
    [string]$VenvPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ExpectedWheelSha256 = "1025A3098D4688E335BC004324A320069BE9083A56C6924539146C7919891844"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$RequirementsFile = Join-Path $RepoRoot "python\requirements\logix-designer-sdk.lock.txt"

if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
    $PythonExecutable = "C:\Program Files\Python313\python.exe"
}

if ([string]::IsNullOrWhiteSpace($LogixDesignerSdkWheel)) {
    $LogixDesignerSdkWheel = "C:\Users\Public\Documents\Studio 5000\Logix Designer SDK\python\logix_designer_sdk-2.0.2-py3-none-any.whl"
}

if ([string]::IsNullOrWhiteSpace($VenvPath)) {
    $VenvPath = Join-Path $RepoRoot ".venv-logix-designer"
}

function Invoke-Checked {
    param(
        [string]$Executable,
        [string[]]$Arguments,
        [string]$Description
    )

    Write-Host $Description
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

try {
    if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
        throw "Python 3.13 was not found at '$PythonExecutable'. Install Python 3.13 x64 for all users or pass -PythonExecutable."
    }

    if (-not (Test-Path -LiteralPath $LogixDesignerSdkWheel -PathType Leaf)) {
        throw "The Logix Designer SDK wheel was not found at '$LogixDesignerSdkWheel'."
    }

    if (-not (Test-Path -LiteralPath $RequirementsFile -PathType Leaf)) {
        throw "The dependency lock file was not found at '$RequirementsFile'."
    }

    Invoke-Checked `
        -Executable $PythonExecutable `
        -Arguments @(
            "-c",
            "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) else 13)"
        ) `
        -Description "Validating Python 3.13"

    $actualWheelSha256 = (Get-FileHash -LiteralPath $LogixDesignerSdkWheel -Algorithm SHA256).Hash
    if ($actualWheelSha256 -ine $ExpectedWheelSha256) {
        throw "Logix Designer SDK wheel SHA256 mismatch. Expected $ExpectedWheelSha256, received $actualWheelSha256."
    }

    if (-not (Test-Path -LiteralPath (Join-Path $VenvPath "Scripts\python.exe") -PathType Leaf)) {
        Invoke-Checked `
            -Executable $PythonExecutable `
            -Arguments @("-m", "venv", $VenvPath) `
            -Description "Creating Logix Designer virtual environment at '$VenvPath'"
    }

    $VenvPython = Join-Path $VenvPath "Scripts\python.exe"
    Invoke-Checked `
        -Executable $VenvPython `
        -Arguments @(
            "-c",
            "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) else 13)"
        ) `
        -Description "Validating virtual environment Python 3.13"

    Invoke-Checked `
        -Executable $VenvPython `
        -Arguments @(
            "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
            "--requirement", $RequirementsFile
        ) `
        -Description "Installing pinned Logix Designer SDK dependencies"

    Invoke-Checked `
        -Executable $VenvPython `
        -Arguments @(
            "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
            "--no-deps", "--force-reinstall", $LogixDesignerSdkWheel
        ) `
        -Description "Installing verified local Logix Designer SDK wheel"

    Invoke-Checked `
        -Executable $VenvPython `
        -Arguments @("-m", "pip", "check") `
        -Description "Checking installed Python dependencies"

    Invoke-Checked `
        -Executable $VenvPython `
        -Arguments @(
            "-c",
            "from importlib.metadata import version; import logix_designer_sdk; print('logix-designer-sdk=' + version('logix-designer-sdk'))"
        ) `
        -Description "Validating Logix Designer SDK import"

    Write-Host "Logix Designer virtual environment setup completed successfully."
    exit 0
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 2
}
