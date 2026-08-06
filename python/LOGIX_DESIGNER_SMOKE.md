# Logix Designer Python read-only smoke

This milestone proves that the Logix Designer Python SDK can safely open and close a disposable ACD copy. Executable enumeration and project inventory are separate, explicit read-only operations. Controller-facing operations remain out of scope.

## Proven interactive baseline

Validated on `NIA-AUTO-ECH-01` with Studio 5000 Logix Designer 36.04:

- Python 3.13.14 x64
- `logix-designer-sdk` 2.0.2
- `cffi` 2.1.0, `clr_loader` 0.3.1, `numpy` 2.5.1, `pycparser` 3.0, `pythonnet` 3.1.0
- Logix Designer SDK wheel SHA-256: `1025A3098D4688E335BC004324A320069BE9083A56C6924539146C7919891844`

On 2026-07-23, Rockwell's official `get_processor_type.py 36` returned the V36 processor catalog, including `1756-L85E`. The official `get_comm_path.py` then opened a disposable copy of `ExampleForCICD_L85E.ACD`, read `EmulateEthernet\127.0.0.1`, closed the project, and exited 0.

## Safety boundary

The repository probe never passes the source project to the SDK. It:

1. Calculates the source SHA-256.
2. Creates and verifies a disposable copy with the same extension.
3. Opens and closes only that copy.
4. Verifies the source SHA-256 is unchanged.
5. Removes the disposable copy.

It does not save, download, upload, change communications paths, go online, or communicate with a controller. Executable enumeration occurs only when `--enumerate-executables` is supplied. Project inventory occurs only when `--project-inventory` is supplied and reads the project communications path plus executable inventory. Offline build validation occurs only when `--build-validation` is supplied; it calls `build()` on the disposable copy and never calls `save()`.

## Repository venv setup

The repository-owned Python 3.13 environment passed the open/close probe on 2026-07-24 at `C:\Projects\ra-logix-cicd\.venv-logix-designer-system-py313`. It does not modify the proven vendor examples venv.

Use the system-wide Python 3.13 interpreter for interactive repository validation:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File "C:\Projects\ra-logix-cicd\python\scripts\setup_logix_designer_venv.ps1" `
  -PythonExecutable "C:\Program Files\Python313\python.exe" `
  -LogixDesignerSdkWheel "C:\Users\Public\Documents\Studio 5000\Logix Designer SDK\python\logix_designer_sdk-2.0.2-py3-none-any.whl" `
  -VenvPath "C:\Projects\ra-logix-cicd\.venv-logix-designer-system-py313"
```

## Interactive repository probe

Run only after setup completes. The expected result is an exit code of `0`, `"status": "passed"`, and passed open, close, source-integrity, and cleanup checks. The executable-enumeration checks must remain `"not_run"`.

```powershell
& "C:\Projects\ra-logix-cicd\.venv-logix-designer-system-py313\Scripts\python.exe" `
  "C:\Projects\ra-logix-cicd\python\smoke\logix_designer_executables.py" `
  --project "C:\Projects\ra-logix-cicd\1-production-files\ACDs\ExampleForCICD_L85E.ACD" `
  --output "C:\Projects\ra-logix-cicd\python\artifacts\logix-designer-open-project.json" `
  --timeout-seconds 600

$LASTEXITCODE
Get-Content "C:\Projects\ra-logix-cicd\python\artifacts\logix-designer-open-project.json"
```

## Read-only executable enumeration

Only after the open/close probe passes, run the explicit enumeration probe. It calls Rockwell's `get_all_executables()` only after the disposable copy opens, returns deterministic secret-filtered summaries, closes the copy, and performs the same source-integrity and cleanup checks.

```powershell
& "C:\Projects\ra-logix-cicd\.venv-logix-designer-system-py313\Scripts\python.exe" `
  "C:\Projects\ra-logix-cicd\python\smoke\logix_designer_executables.py" `
  --project "C:\Projects\ra-logix-cicd\1-production-files\ACDs\ExampleForCICD_L85E.ACD" `
  --output "C:\Projects\ra-logix-cicd\python\artifacts\logix-designer-executables.json" `
  --timeout-seconds 600 `
  --enumerate-executables

$LASTEXITCODE
Get-Content "C:\Projects\ra-logix-cicd\python\artifacts\logix-designer-executables.json"
```

## Read-only project inventory

Only after executable enumeration passes, run the explicit project-inventory probe. It calls Rockwell's `get_communications_path()` and `get_all_executables()` on the disposable copy, then returns a deterministic JSON artifact containing the communications path and routine/AOI paths. It does not connect to a controller.

```powershell
& "C:\Projects\ra-logix-cicd\.venv-logix-designer-system-py313\Scripts\python.exe" `
  "C:\Projects\ra-logix-cicd\python\smoke\logix_designer_executables.py" `
  --project "C:\Projects\ra-logix-cicd\1-production-files\ACDs\ExampleForCICD_L85E.ACD" `
  --output "C:\Projects\ra-logix-cicd\python\artifacts\logix-designer-project-inventory.json" `
  --timeout-seconds 600 `
  --project-inventory

$LASTEXITCODE
Get-Content "C:\Projects\ra-logix-cicd\python\artifacts\logix-designer-project-inventory.json"
```

## Offline build validation

Rockwell's SDK documentation states that `LogixProject.build()` is supported starting with Logix Designer v37. Earlier releases throw `OperationFailedError`. The lab VM has Logix Designer 36.04, and its manual probe confirmed the documented failure: `Operation not supported on Logix Designer version 36.4.`

Do not add or enable a Jenkins build-validation gate on this VM. The repository probe retains the explicit `--build-validation` diagnostic for future validation after upgrading to Logix Designer v37 or later. It operates only on a disposable copy and never calls `save()`, `download()`, `go_online()`, or another controller-facing method.

```powershell
& "C:\Projects\ra-logix-cicd\.venv-logix-designer-system-py313\Scripts\python.exe" `
  "C:\Projects\ra-logix-cicd\python\smoke\logix_designer_executables.py" `
  --project "C:\Projects\ra-logix-cicd\1-production-files\ACDs\ExampleForCICD_L85E.ACD" `
  --output "C:\Projects\ra-logix-cicd\python\artifacts\logix-designer-build-validation.json" `
  --timeout-seconds 600 `
  --build-validation

$LASTEXITCODE
Get-Content "C:\Projects\ra-logix-cicd\python\artifacts\logix-designer-build-validation.json"
```

## Jenkins gate

`RUN_PYTHON_LOGIX_DESIGNER_SMOKE`, `RUN_PYTHON_LOGIX_DESIGNER_EXECUTABLE_ENUMERATION`, and `RUN_PYTHON_LOGIX_DESIGNER_PROJECT_INVENTORY` all remain `false` by default. Before enabling any of them, install Python 3.13.14 x64 for all users at `C:\Program Files\Python313\python.exe` (or provide an equivalent path through `LOGIX_DESIGNER_PYTHON_EXE`). Jenkins runs as `NT AUTHORITY\SYSTEM`.

The open/close parameter produces `logix-designer-open-project.json`. The enumeration parameter runs a separate stage and produces `logix-designer-executables.json`. The project-inventory parameter produces `logix-designer-project-inventory.json`. All use the Python 3.13 lock, a repository-owned venv, a disposable ACD copy, a 600-second SDK timeout, and archived JSON artifacts.
