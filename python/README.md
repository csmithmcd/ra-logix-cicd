# FactoryTalk Logix Echo Python smoke test

This folder contains the first Python milestone for the Rockwell CI/CD lab. It proves that Python can import the FactoryTalk Logix Echo SDK and perform read-only service queries.

## Safety boundary

The smoke test calls only these documented SDK methods:

- `get_product_info()`
- `get_license_info()`
- `list_chassis()`
- `list_controllers()`

It does not create, update, start, stop, download to, or delete controllers. The JSON artifact records inventory counts, not controller or chassis names.

## Confirmed SDK requirements

- Package: `ftecho_sdk` 4.0.0
- Import: `from ftecho_sdk import ServiceApiClientV2`
- Supported Python: 3.12 or 3.13
- Recommended Python: 3.12 x64
- SDK wheel SHA256: `460F75777CE30CEF72CD606E83BC320DE212B2C1CBB8EB776AA8535CA3B53BA9`

Python was not installed when the SDK inventory was collected. Install Python 3.12 x64 for all users at `C:\Program Files\Python312`. An all-users installation is required so Jenkins running as `NT AUTHORITY\SYSTEM` can use it.

## Manual test before Jenkins

Run these commands in Windows PowerShell from the repository root:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File ".\python\scripts\setup_venv.ps1"

& ".\.venv\Scripts\python.exe" `
  ".\python\smoke\echo_connectivity.py" `
  --output ".\python\artifacts\echo-connectivity.json"

$LASTEXITCODE
Get-Content ".\python\artifacts\echo-connectivity.json"
```

A successful test returns exit code `0` and writes a JSON artifact with `"status": "passed"`.

## Jenkins integration

`Jenkinsfile.smoke` keeps the Python stages disabled by default. After the manual test passes:

1. Open the `Rockwell-CICD-Smoke` job.
2. Choose **Build with Parameters**.
3. Enable `RUN_PYTHON_ECHO_SMOKE`.
4. Run the build.

Jenkins creates its own repository-local `.venv`, runs the same read-only test under `NT AUTHORITY\SYSTEM`, and archives `python/artifacts/echo-connectivity.json`.

## Exit codes

| Code | Meaning |
|---:|---|
| 0 | Success |
| 1 | Unexpected test failure |
| 2 | Configuration error |
| 3 | Python dependency or SDK import error |
| 4 | Echo service unavailable or SDK service error |
| 5 | Cleanup failure, reserved for later lifecycle tests |

## Rollback

The Jenkins parameter defaults to disabled, so clearing `RUN_PYTHON_ECHO_SMOKE` immediately restores the previous pipeline behavior.

To remove the repository change after it is committed, use `git revert <python-smoke-commit>`. Generated files can be removed separately:

```powershell
$repo = (Resolve-Path ".").Path
Remove-Item -LiteralPath (Join-Path $repo ".venv") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $repo "python\artifacts") -Recurse -Force -ErrorAction SilentlyContinue
```

No controller rollback is required because the smoke test performs no controller mutations.
