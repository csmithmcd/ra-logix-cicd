[CmdletBinding()]
param(
    [string]$PythonExecutable = "",
    [string]$EchoSdkWheel = "",
    [string]$VenvPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ExpectedWheelSha256 = "460F75777CE30CEF72CD606E83BC320DE212B2C1CBB8EB776AA8535CA3B53BA9"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$RequirementsFile = Join-Path $RepoRoot "python\requirements\echo-sdk.lock.txt"

if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
    $PythonExecutable = "C:\Program Files\Python312\python.exe"
}

if ([string]::IsNullOrWhiteSpace($EchoSdkWheel)) {
    $EchoSdkWheel = "C:\Users\Public\Documents\FactoryTalk Logix Echo\SDK\Python\ftecho_sdk-4.0.0-py3-none-any.whl"
}

if ([string]::IsNullOrWhiteSpace($VenvPath)) {
    $VenvPath = Join-Path $RepoRoot ".venv"
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
        throw "Python 3.12 was not found at '$PythonExecutable'. Install Python 3.12 x64 for all users or pass -PythonExecutable."
    }

    if (-not (Test-Path -LiteralPath $EchoSdkWheel -PathType Leaf)) {
        throw "The Echo SDK wheel was not found at '$EchoSdkWheel'."
    }

    if (-not (Test-Path -LiteralPath $RequirementsFile -PathType Leaf)) {
        throw "The dependency lock file was not found at '$RequirementsFile'."
    }

    Invoke-Checked `
        -Executable $PythonExecutable `
        -Arguments @(
            "-c",
            "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 12)"
        ) `
        -Description "Validating Python 3.12"

    $actualWheelSha256 = (Get-FileHash -LiteralPath $EchoSdkWheel -Algorithm SHA256).Hash
    if ($actualWheelSha256 -ine $ExpectedWheelSha256) {
        throw "Echo SDK wheel SHA256 mismatch. Expected $ExpectedWheelSha256, received $actualWheelSha256."
    }

    if (-not (Test-Path -LiteralPath (Join-Path $VenvPath "Scripts\python.exe") -PathType Leaf)) {
        Invoke-Checked `
            -Executable $PythonExecutable `
            -Arguments @("-m", "venv", $VenvPath) `
            -Description "Creating repository virtual environment at '$VenvPath'"
    }

    $VenvPython = Join-Path $VenvPath "Scripts\python.exe"
    Invoke-Checked `
        -Executable $VenvPython `
        -Arguments @(
            "-c",
            "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 12)"
        ) `
        -Description "Validating virtual environment Python version"

    Invoke-Checked `
        -Executable $VenvPython `
        -Arguments @(
            "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
            "--requirement", $RequirementsFile
        ) `
        -Description "Installing pinned Echo SDK dependencies"

    Invoke-Checked `
        -Executable $VenvPython `
        -Arguments @(
            "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
            "--no-deps", "--force-reinstall", $EchoSdkWheel
        ) `
        -Description "Installing verified local Echo SDK wheel"

    Invoke-Checked `
        -Executable $VenvPython `
        -Arguments @("-m", "pip", "check") `
        -Description "Checking installed Python dependencies"

    Invoke-Checked `
        -Executable $VenvPython `
        -Arguments @(
            "-c",
            "from importlib.metadata import version; import ftecho_sdk; print('ftecho_sdk=' + version('ftecho_sdk'))"
        ) `
        -Description "Validating Echo SDK import"

    Write-Host "Virtual environment setup completed successfully."
    exit 0
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 2
}
