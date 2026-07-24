# Logix Designer Python open-project probe

The current milestone is to prove that `logix-designer-sdk` 2.0.2 can open a known-good ACD interactively. Executable enumeration and Jenkins execution remain gated until Rockwell's official `get_comm_path.py` example succeeds.

## Current failure boundary

Python 3.12 imports the SDK successfully, but both the custom probe and Rockwell's official example stall inside:

```python
await LogixProject.open_logix_project(...)
```

The failure occurs before `get_all_executables()`. Cancellation-related pythonnet errors after `Ctrl+C` are aftermath, not the root cause.

## Safety boundary

The custom probe never passes the source project to the SDK. It:

1. Calculates the source SHA256.
2. Creates and verifies a disposable copy with the same extension.
3. Attempts only `LogixProject.open_logix_project()`.
4. Calls `project.close()` if open returns.
5. Confirms the source SHA256 did not change.
6. Deletes the disposable copy.

It does not enumerate executables, save, build, download, upload, change communications paths, go online, or communicate with a controller.

## Confirmed requirements

- Package: `logix-designer-sdk` 2.0.2
- Supported Python: 3.12 or 3.13
- Recommended Python: 3.12 x64
- Unsupported Python: 3.14
- SDK wheel SHA256: `1025A3098D4688E335BC004324A320069BE9083A56C6924539146C7919891844`
- Maximum probe timeout: 600 seconds

## Gate 1: official Rockwell example

Run this interactively on the Rockwell VM against the known-good ACD:

```powershell
$python = ".\.venv-logix-designer\Scripts\python.exe"
$example = "C:\Users\Public\Documents\Studio 5000\Logix Designer SDK\python\Examples\get_comm_path.py"
$acd = ".\1-production-files\ACDs\ExampleForCICD_L85E.ACD"

& $python $example $acd
$LASTEXITCODE
```

If this hangs or fails, stop. Do not run the Jenkins stage or executable enumeration.

## Gate 2: custom diagnostic probe

Only after the official example succeeds, run:

```powershell
& ".\.venv-logix-designer\Scripts\python.exe" `
  ".\python\smoke\logix_designer_executables.py" `
  --project ".\1-production-files\ACDs\ExampleForCICD_L85E.ACD" `
  --output ".\python\artifacts\logix-designer-executables.json" `
  --timeout-seconds 600

$LASTEXITCODE
Get-Content ".\python\artifacts\logix-designer-executables.json"
```

On timeout, the JSON identifies `open_logix_project` as the failing operation and captures:

- Windows identity and session
- `LdSdkServer` process state
- TCP connections on port 53204
- installed Studio 5000 / Logix Designer versions
- source and disposable-copy paths

The checks for `get_all_executables` remain `not_run` until the open-project gate is proven.

## Jenkins gate

Keep `RUN_PYTHON_LOGIX_DESIGNER_SMOKE` disabled. It defaults to `false`. Do not enable it until both interactive gates succeed and the probe is deliberately advanced to the next milestone.

No controller rollback is required because all current operations are read-only and use a disposable project copy.
