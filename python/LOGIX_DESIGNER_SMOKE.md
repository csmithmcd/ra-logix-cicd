# Logix Designer Python read-only smoke

This milestone proves that the Logix Designer Python SDK can safely open and close a disposable ACD copy. Executable enumeration, Jenkins execution, and all controller-facing operations remain separately gated.

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

It does not enumerate executables, save, build, download, upload, change communications paths, go online, or communicate with a controller.

## Repository venv setup

The repository-owned Python 3.13 environment passed the open/close probe on 2026-07-24 at `C:\Projects\ra-logix-cicd\.venv-logix-designer-py313`. It does not modify the proven vendor examples venv.

The current interactive base interpreter is installed per-user. Use it for the interactive repository validation:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File "C:\Projects\ra-logix-cicd\python\scripts\setup_logix_designer_venv.ps1" `
  -PythonExecutable "C:\Users\csmith\AppData\Local\Programs\Python\Python313\python.exe" `
  -LogixDesignerSdkWheel "C:\Users\Public\Documents\Studio 5000\Logix Designer SDK\python\logix_designer_sdk-2.0.2-py3-none-any.whl" `
  -VenvPath "C:\Projects\ra-logix-cicd\.venv-logix-designer-py313"
```

## Interactive repository probe

Run only after setup completes. The expected result is an exit code of `0`, `"status": "passed"`, and passed open, close, source-integrity, and cleanup checks. The executable-enumeration checks must remain `"not_run"`.

```powershell
& "C:\Projects\ra-logix-cicd\.venv-logix-designer-py313\Scripts\python.exe" `
  "C:\Projects\ra-logix-cicd\python\smoke\logix_designer_executables.py" `
  --project "C:\Projects\ra-logix-cicd\1-production-files\ACDs\ExampleForCICD_L85E.ACD" `
  --output "C:\Projects\ra-logix-cicd\python\artifacts\logix-designer-open-project.json" `
  --timeout-seconds 600

$LASTEXITCODE
Get-Content "C:\Projects\ra-logix-cicd\python\artifacts\logix-designer-open-project.json"
```

## Jenkins gate

`RUN_PYTHON_LOGIX_DESIGNER_SMOKE` remains `false` by default. Before enabling it, install Python 3.13.14 x64 for all users at `C:\Program Files\Python313\python.exe` (or provide an equivalent path through `LOGIX_DESIGNER_PYTHON_EXE`). Jenkins runs as `NT AUTHORITY\SYSTEM` and must not depend on the per-user interpreter above.

When deliberately enabled, Jenkins uses the Python 3.13 lock, a repository-owned venv, a disposable copy of the known-good ACD, a 600-second SDK timeout, and archives the JSON artifact. Do not enable executable enumeration until a separate review advances the read-only scope.
