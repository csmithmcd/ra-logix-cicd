# Logix Git code comparison

`compare/logix_code_compare.py` compares routine logic from two committed Git revisions of the same Logix project. It is intentionally report-only: routine differences produce exit code `0`; operational failures produce a nonzero exit code.

## How it works

1. Resolves the requested base and current Git commits and project blobs.
2. Materializes both ACD/L5X blobs in an ignored disposable directory without changing the working tree.
3. Opens the two projects sequentially with the Logix Designer SDK.
4. Calls `get_all_executables()` and exports every returned routine with `partial_export_to_xml_file()`.
5. Removes volatile `ExportDate` metadata, orders XML attributes, and hashes the normalized L5X.
6. Reports added, removed, modified, and unchanged routine paths.
7. Closes both projects, deletes all disposable files, and verifies the repository status is unchanged.

The script never saves a project, downloads, uploads, goes online, changes a communications path, or calls a controller-facing method. Partial export is supported by the installed Logix Designer v36.04 environment.

## Manual validation

Run from the repository root. The comparator records the existing Git status and verifies that it remains unchanged, so the new Python files may still be uncommitted during manual validation:

```powershell
& "C:\Projects\ra-logix-cicd\.venv-logix-designer-system-py313\Scripts\python.exe" `
  "C:\Projects\ra-logix-cicd\python\compare\logix_code_compare.py" `
  --repository "C:\Projects\ra-logix-cicd" `
  --project-path "1-production-files/ACDs/ExampleForCICD_L85E.ACD" `
  --base-revision "HEAD^" `
  --current-revision "HEAD" `
  --output-json "C:\Projects\ra-logix-cicd\python\artifacts\logix-code-compare.json" `
  --output-markdown "C:\Projects\ra-logix-cicd\python\artifacts\logix-code-compare.md" `
  --timeout-seconds 600

$LASTEXITCODE
Get-Content "C:\Projects\ra-logix-cicd\python\artifacts\logix-code-compare.md"
```

If the ACD blob is unchanged between the two commits, the expected comparison status is `identical`. If it changed, the expected status is `different`; this still returns exit code `0` and records the routine changes in both artifacts.

## Current scope

The first milestone compares executable routine paths returned by `get_all_executables()`. It does not yet compare controller tags, data types, AOIs, modules, tasks, or project properties. Jenkins integration remains disabled until the manual comparison succeeds on the Rockwell VM.
