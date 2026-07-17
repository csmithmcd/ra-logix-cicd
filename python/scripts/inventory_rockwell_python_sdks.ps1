[CmdletBinding()]
param(
    [string[]]$SdkRoots = @(
        "C:\Users\Public\Documents\FactoryTalk Logix Echo\SDK\Python",
        "C:\Users\Public\Documents\Studio 5000\Logix Designer SDK\python"
    ),
    [string]$OutputDirectory = (Join-Path `
        ([Environment]::GetFolderPath("Desktop")) `
        ("rockwell-sdk-inventory-{0}" -f (Get-Date -Format "yyyyMMdd-HHmmss")))
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-SdkLabel {
    param([string]$Path)

    if ($Path -match "Echo") {
        return "FactoryTalkLogixEcho"
    }

    if ($Path -match "Designer") {
        return "LogixDesigner"
    }

    return "Sdk"
}

function Get-RelativePath {
    param(
        [string]$Root,
        [string]$Path
    )

    $normalizedRoot = $Root.TrimEnd("\") + "\"
    if ($Path.StartsWith($normalizedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $Path.Substring($normalizedRoot.Length)
    }

    return [System.IO.Path]::GetFileName($Path)
}

function Get-PythonCommandInfo {
    $results = New-Object System.Collections.Generic.List[object]

    foreach ($name in @("py", "python", "python3")) {
        $command = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -ne $command) {
            $versionOutput = ""
            try {
                $versionOutput = (& $command.Source --version 2>&1 | Out-String).Trim()
            }
            catch {
                $versionOutput = $_.Exception.Message
            }

            $results.Add([pscustomobject]@{
                Name = $name
                Path = $command.Source
                Version = $versionOutput
            })
        }
    }

    return $results.ToArray()
}

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$evidenceRoot = Join-Path $OutputDirectory "evidence"
New-Item -ItemType Directory -Path $evidenceRoot -Force | Out-Null

Add-Type -AssemblyName System.IO.Compression.FileSystem

$rootRecords = New-Object System.Collections.Generic.List[object]
$fileRecords = New-Object System.Collections.Generic.List[object]
$wheelRecords = New-Object System.Collections.Generic.List[object]
$missingRoots = New-Object System.Collections.Generic.List[string]

foreach ($sdkRoot in $SdkRoots) {
    $label = Get-SdkLabel -Path $sdkRoot
    $exists = Test-Path -LiteralPath $sdkRoot -PathType Container

    $rootRecords.Add([pscustomobject]@{
        Label = $label
        Path = $sdkRoot
        Exists = $exists
    })

    if (-not $exists) {
        $missingRoots.Add($sdkRoot)
        continue
    }

    $files = @(Get-ChildItem -LiteralPath $sdkRoot -Recurse -File | Sort-Object FullName)
    foreach ($file in $files) {
        $relativePath = Get-RelativePath -Root $sdkRoot -Path $file.FullName
        $sha256 = $null

        if ($file.Extension -ieq ".whl") {
            $sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
        }

        $fileRecords.Add([pscustomobject]@{
            Sdk = $label
            RelativePath = $relativePath
            Length = $file.Length
            LastWriteTimeUtc = $file.LastWriteTimeUtc.ToString("o")
            Sha256 = $sha256
        })

        $isTextExtension = $file.Extension -in @(
            ".md", ".txt", ".rst", ".json", ".toml", ".yaml", ".yml", ".py", ".ps1"
        )
        $isEvidencePath = (
            $file.Name -match "^(readme|requirements|license|notice)" -or
            $file.DirectoryName -match "(sample|samples|example|examples|demo|docs|documentation)"
        )

        if ($isTextExtension -and $isEvidencePath -and $file.Length -le 2MB) {
            $destination = Join-Path (Join-Path $evidenceRoot $label) $relativePath
            New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
            Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
        }

        if ($file.Extension -ine ".whl") {
            continue
        }

        $wheelEvidenceRoot = Join-Path `
            (Join-Path $evidenceRoot "wheel-metadata") `
            ([System.IO.Path]::GetFileNameWithoutExtension($file.Name))
        New-Item -ItemType Directory -Path $wheelEvidenceRoot -Force | Out-Null

        $archive = [System.IO.Compression.ZipFile]::OpenRead($file.FullName)
        try {
            $entryNames = @($archive.Entries | ForEach-Object { $_.FullName })
            $topLevelEntries = @(
                $entryNames |
                    Where-Object { $_ -notmatch "\.dist-info/" -and $_ -notmatch "\.data/" } |
                    ForEach-Object { ($_ -split "/")[0] } |
                    Where-Object { $_ -and $_ -notmatch "^__pycache__$" } |
                    Sort-Object -Unique
            )

            foreach ($entry in $archive.Entries) {
                if ($entry.FullName -notmatch "\.dist-info/(METADATA|WHEEL|top_level\.txt|entry_points\.txt)$") {
                    continue
                }

                $destinationName = ($entry.FullName -replace '[\\/:*?"<>|]', "_")
                $destination = Join-Path $wheelEvidenceRoot $destinationName
                [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $destination, $true)
            }

            $wheelRecords.Add([pscustomobject]@{
                Sdk = $label
                FileName = $file.Name
                RelativePath = $relativePath
                Length = $file.Length
                Sha256 = $sha256
                TopLevelEntries = $topLevelEntries
                EntryNames = $entryNames
            })
        }
        finally {
            $archive.Dispose()
        }
    }
}

$services = @(
    Get-Service -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -match "Echo|Rockwell|FactoryTalk" -or
            $_.DisplayName -match "Echo|Rockwell|FactoryTalk"
        } |
        Select-Object Status, Name, DisplayName
)

$inventory = [ordered]@{
    GeneratedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    ComputerName = $env:COMPUTERNAME
    PowerShellVersion = $PSVersionTable.PSVersion.ToString()
    OperatingSystem = [pscustomobject]@{
        Version = [Environment]::OSVersion.VersionString
        Architecture = $env:PROCESSOR_ARCHITECTURE
    }
    PythonCommands = @(Get-PythonCommandInfo)
    RockwellServices = $services
    SdkRoots = $rootRecords.ToArray()
    Files = $fileRecords.ToArray()
    Wheels = $wheelRecords.ToArray()
}

$jsonPath = Join-Path $OutputDirectory "inventory.json"
$inventory | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $jsonPath -Encoding UTF8

$summaryPath = Join-Path $OutputDirectory "summary.txt"
@(
    "Rockwell Python SDK inventory"
    "Generated: $($inventory.GeneratedAtUtc)"
    "Computer: $($inventory.ComputerName)"
    "SDK roots found: $(@($rootRecords | Where-Object Exists).Count) / $($rootRecords.Count)"
    "Files inventoried: $($fileRecords.Count)"
    "Wheels found: $($wheelRecords.Count)"
    "Missing SDK roots: $($missingRoots.Count)"
) | Set-Content -LiteralPath $summaryPath -Encoding UTF8

$zipPath = "$OutputDirectory.zip"
if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
Compress-Archive -LiteralPath $OutputDirectory -DestinationPath $zipPath -CompressionLevel Optimal

Write-Host "Inventory folder: $OutputDirectory"
Write-Host "Upload this archive to Codex: $zipPath"

if ($missingRoots.Count -gt 0) {
    Write-Error ("Missing SDK roots: {0}" -f ($missingRoots -join "; ")) -ErrorAction Continue
    exit 2
}

if ($wheelRecords.Count -eq 0) {
    Write-Error "No Python wheel files were found in the SDK roots." -ErrorAction Continue
    exit 3
}

exit 0
