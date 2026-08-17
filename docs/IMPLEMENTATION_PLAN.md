# Implementation Plan — Rockwell CI/CD Lab

**Last updated:** 2026-08-17  
**Branch:** `lab`

See [ARCHITECTURE.md](ARCHITECTURE.md) for decisions, constraints, and the target pipeline diagram.

---

## Status Legend

| Symbol | Meaning |
|---|---|
| ✅ | Complete and committed |
| 🔶 | Implemented but not yet run under Jenkins / not fully validated |
| ⬜ | Not started |
| 🚫 | Blocked — dependency unresolved |

---

## Phase 1 — Jenkins + .NET Infrastructure

**Goal:** Prove Jenkins, Git, NuGet, and the C# build chain work end-to-end under `NT AUTHORITY\SYSTEM`.

| Task | Status | Notes |
|---|---|---|
| Jenkinsfile.smoke with infrastructure stages | ✅ | `Rockwell-CICD-Smoke` job, successful |
| Repository-level NuGet.Config | ✅ | Clears per-user sources; adds `C:\data\nuget\rockwell` |
| Modernize C# solution to net8/net10 | ✅ | Zero warnings, zero errors |
| Archive release artifacts | ✅ | DLLs fingerprinted |
| Smoke job passes: checkout → restore → build → verify | ✅ | |

**Phase 1 is complete. All gates passed.**

---

## Phase 2 — Echo SDK Read-Only Connectivity

**Goal:** Prove Python can import the Echo SDK and query the service, including under `NT AUTHORITY\SYSTEM`.

| Task | Status | Notes |
|---|---|---|
| SDK inventory (wheel name, import, Python version) | ✅ | `ftecho_sdk` 4.0.0, Python 3.13 |
| `echo-sdk.lock.txt` (pinned dependencies) | ✅ | cffi, clr-loader, pycparser, pythonnet, typeguard |
| `setup_venv.ps1` (repo-owned venv from local wheel) | ✅ | Creates `.venv` at repository root |
| `echo_connectivity.py` (read-only probe) | ✅ | Queries product info, license, chassis, controllers |
| JSON artifact with structured output | ✅ | `python/artifacts/echo-connectivity.json` |
| Exit codes and structured logging | ✅ | |
| Manual test validated interactively | ✅ | Passed on Python 3.12 (2026-07-17) and Python 3.13 (2026-08-17). Exit 0, all checks passed. 9 controllers visible in chassis. License valid. |
| Jenkins `RUN_PYTHON_ECHO_SMOKE` stage enabled and passing | ✅ | Build #13, 2026-08-17. Exit 0, all 5 checks passed under `NT AUTHORITY\SYSTEM`. Python 3.13.14. |

**Phase 2 is complete. All gates passed.**

> **Note — stale venv:** Build #11 failed because the Jenkins workspace `.venv` had been created by a prior build using Python 3.12 (`C:\Program Files\Python312`). The `setup_venv.ps1` skip-if-exists logic preserved the stale venv. The workaround was to manually delete `C:\data\jenkins_home\workspace\Rockwell-CICD-Smoke\.venv` before re-running. Future improvement: add `pyvenv.cfg` version validation to `setup_venv.ps1` so it recreates the venv when the base Python changes.

---

## Phase 3 — Logix Designer SDK Read-Only Operations

**Goal:** Prove the Logix Designer SDK can open, inspect, and close a project safely.

| Task | Status | Notes |
|---|---|---|
| SDK inventory (wheel, Python version, dependencies) | ✅ | `logix-designer-sdk` 2.0.2, Python 3.13 |
| `logix-designer-sdk.lock.txt` (pinned dependencies) | ✅ | cffi, clr_loader, numpy, pycparser, pythonnet |
| `setup_logix_designer_venv.ps1` (separate repo venv) | ✅ | Creates `.venv-logix-designer` |
| `logix_designer_executables.py` (open/close probe) | ✅ | Disposable copy, SHA-256 integrity checks |
| Open/close ACD (ExampleForCICD_L85E) | ✅ | Validated interactively on 2026-07-23 |
| Executable enumeration (`get_all_executables()`) | ✅ | Validated interactively |
| Project inventory (`get_communications_path()`) | ✅ | Returns `EmulateEthernet\127.0.0.1` |
| Offline build validation (`build()`) | ✅ | Confirmed NOT supported on v36.04, requires v37+ |
| Jenkins `RUN_PYTHON_LOGIX_DESIGNER_SMOKE` stage | ✅ | Build #16, 2026-08-17. Exit 0, open/close passed. 49s. |
| Git-aware routine code comparison | ✅ | `compare/logix_code_compare.py` — see Phase 3a |

**Phase 3 is complete. All gates passed.**

> **Interactive desktop solution:** `NT AUTHORITY\SYSTEM` (session 0) cannot open ACD files — confirmed in build #14. Resolved with a Windows Scheduled Task (`LD-SDK-Run`) using the `INTERACTIVE` group principal. Jenkins (SYSTEM) writes a request to `C:\data\ld-runner\request.json`, triggers the task via `schtasks /run`, and polls for `result.exitcode`. The task runs in the active console session (currently `BMCD\csmith`; `logix-runner` local account configured for auto-logon on next reboot). New scripts: `python/scripts/invoke_ld_sdk_interactive.ps1` (Jenkins wrapper) and `python/scripts/ld_sdk_task_action.ps1` (task body).
>
> **PS 5.1 encoding note:** PowerShell 5.1 reads `.ps1` files as Windows-1252. Non-ASCII characters (em dash U+2014) in strings cause parse errors. All PowerShell scripts in this repo must use plain ASCII only.

---

## Phase 3a — Git-Aware Routine Code Comparison

**Goal:** Compare routine logic between two Git commits using the Logix Designer SDK.

| Task | Status | Notes |
|---|---|---|
| `logix_code_compare.py` | ✅ | Materializes two ACD blobs, opens sequentially, exports with `partial_export_to_xml_file()`, normalizes XML, hashes, diffs |
| Markdown + JSON output | ✅ | Reports added/removed/modified/unchanged routines |
| Source integrity verification | ✅ | SHA-256 before and after, working tree status checked |
| Manual validation on NIA-AUTO-ECH-01 | ✅ | 2026-08-17. Exit 0, identical (HEAD vs HEAD^ same ACD blob). 23/23 checks passed. 104s. |
| Jenkins integration | ✅ | Build #18, 2026-08-17. Exit 0. 112s. Active session detected, task routed through csmith's interactive desktop. |

**Next action:** Run `logix_code_compare.py` manually with `--base-revision HEAD^ --current-revision HEAD`. Confirm exit 0 and a valid comparison result before adding a Jenkins stage.

---

## Phase 4 — Echo Controller Lifecycle

**Goal:** Create an Echo controller, download a project to it, and put it in RUN mode programmatically.

| Task | Status | Notes |
|---|---|---|
| Investigate Echo SDK controller lifecycle API | ✅ | `ServiceApiClientV2`: `create_controller`, `download`, `update_controller`, `delete_controller`. `get_controller_info_from_acd` reads firmware GUID and slot from the ACD. |
| Controller naming convention | ✅ | `CICD_TEST_01` in dedicated `CICD_TEST_CHASSIS` |
| `python/deploy/echo_deploy.py` | ✅ | Idempotent pre-flight, `try/finally` cleanup, 12/12 checks |
| Cleanup: stop and delete controller after each run | ✅ | Controller + chassis both deleted in `finally` |
| JSON artifact with controller state evidence | ✅ | `python/artifacts/echo-deploy.json` |
| Jenkins `RUN_PYTHON_ECHO_DEPLOY` stage | 🔶 | Added to Jenkinsfile.smoke. Not yet run under Jenkins. |
| Manual validation | ✅ | 2026-08-17. Exit 0. 74s. `controller_mode: HARD_RUN`, `is_in_run_mode: true`, `loaded_project: ExampleWithCICD_L85E`. |

**Phase 4 is manually validated. Jenkins gate pending.**

> **Implementation notes:**
> - `ExampleForCICD_L85E.ACD` needs a ControlLogix chassis (not chassis-less) and has slot=0 baked in. A dedicated `CICD_TEST_CHASSIS` is created so the controller can occupy slot 0 without conflicting with existing chassis controllers.
> - IP address: the ACD embeds `10.243.7.84` but all existing chassis controllers also use it. CICD_TEST_01 uses `127.0.0.2` (loopback alias, no adapter config needed on Windows).
> - `update_controller` requires `Name` to be non-null. Must call `read_controller` first, use `to_controller_update()`, then modify the target field.
> - `is_enabled=True` must be set at `create_controller` time (not after). Download fails on a stopped controller.
> - Runs under `NT AUTHORITY/SYSTEM` — no interactive desktop required. Echo SDK is session-independent.

---

## Phase 5 — Tag Reads and Writes

**Goal:** Confirm that the Echo SDK can read and write controller tags against a running simulated controller.

| Task | Status | Notes |
|---|---|---|
| Investigate Echo SDK tag access API | ⬜ | Need to find: read_tag, write_tag equivalents |
| Read a known tag from ExampleForCICD_L85E | ⬜ | |
| Write a disposable test tag and verify the write | ⬜ | |
| Restore the original value after the test | ⬜ | |
| JSON output with before/after values | ⬜ | |

---

## Phase 6 — VCS Tooling Investigation (l5xplode)

**Goal:** Determine whether Rockwell's VCS Custom Tools are installed and whether an implode operation (exploded source → L5X) is available.

This phase runs in parallel with Phases 4–5 and does not block them.

| Task | Status | Notes |
|---|---|---|
| Check whether L5XExploder is installed on NIA-AUTO-ECH-01 | ✅ | Not pre-installed. Current tool is `l5xplode` (not `L5XExploder`). Must build from source. |
| Identify current tool name and version | ✅ | `l5xplode` — https://github.com/RockwellAutomation/ra-logix-designer-vcs-custom-tools — cloned to `C:\Projects\ra-logix-designer-vcs-custom-tools`. Built with .NET 10.0 in 36s (0 warnings). |
| Confirm `implode` / `explode` command syntax | ✅ | `l5xplode explode --l5x <src.L5X> --dir <outDir> [--force]` / `l5xplode implode --dir <outDir> --l5x <out.L5X> [--force]` |
| Explode ExampleForCICD_L85E.L5X into a test source folder | ✅ | 2026-08-17. Exit 0. Output: `1-production-files/Source/RSLogix5000Content/`. 47 files: 5 programs, 25 tags, 12 routines, 2 tasks, 1 data type, 1 module. |
| Implode back and diff against original L5X | ✅ | Exit 0. Semantically lossless: `ExportDate` stripped (by design, reduces git noise), `encoding="UTF-8"` → `utf-8`, whitespace normalized. Engineering data is identical. |

**Phase 6 is complete. VCS tooling is available. Phase 9 can proceed.**

> **Implementation notes:**
> - `l5xplode` (explode/implode) works without Studio 5000 SDK — can run as `NT AUTHORITY\SYSTEM` in Jenkins.
> - `l5xgit` (ACD↔L5X conversion, difftool, commit) requires Studio 5000 SDK — needs the `LD-SDK-Run` interactive session path.
> - The `--unsafe-skip-dependency-check` flag is needed for L5X files exported without the Dependencies option (common in Logix Designer without that export option selected).
> - Round-trip is semantically lossless; byte-identical is not guaranteed and not required.
> - The tool must be built from source. Add a `setup_l5xplode.ps1` step to the Jenkins pipeline or pre-build and deploy the binary to `C:\data\`.

**If VCS tools are not installed:** The single L5X remains the source of truth. The exploded source architecture (Phase 8) is deferred.

**If VCS tools are installed (current state — `l5xplode` built from source):** The planned directory structure becomes:

```
Source/                   ← engineers commit this
    Controller/
        Programs/
        Tags/
        DataTypes/
        AOIs/
Build/                    ← generated, never committed
    Controller.L5X
    Controller.ACD
```

---

## Phase 7 — YAML Test Framework

**Goal:** Engineers write test cases in YAML. The Python framework executes them against a running Echo controller. Results are emitted as JUnit XML and JSON.

| Task | Status | Notes |
|---|---|---|
| Define YAML test case schema | ⬜ | See ADR-008 in ARCHITECTURE.md for draft format |
| `rockwell_ci/test.py` — load YAML, execute, collect results | ⬜ | |
| Tag write → wait → tag read → assert pattern | ⬜ | With configurable timeout |
| JUnit XML output (Jenkins-compatible) | ⬜ | |
| JSON output (structured, archivable) | ⬜ | |
| Unit tests for the YAML parser (no hardware required) | ⬜ | |
| Sample test cases for ExampleForCICD_L85E | ⬜ | Based on existing Excel test cases as reference |

---

## Phase 8 — `rockwell-ci` Package and Full Pipeline

**Goal:** Consolidate all stages into a proper Python package with a unified CLI. Replace `Jenkinsfile.smoke` with a production `Jenkinsfile`.

| Task | Status | Notes |
|---|---|---|
| `rockwell_ci/` package with `cli.py` entry point | ⬜ | |
| `rockwell-ci validate` — source XML validation | ⬜ | Fails fast before SDK calls |
| `rockwell-ci build` — L5X → ACD via Logix Designer SDK | ⬜ | Requires understanding `save()` / `export()` API |
| `rockwell-ci deploy` — ACD → Echo controller | ⬜ | Pulls from Phase 4 |
| `rockwell-ci test` — YAML cases → JUnit XML | ⬜ | Pulls from Phase 7 |
| `rockwell-ci compare` — routine diff | ⬜ | Pulls from Phase 3a |
| `rockwell-ci pipeline` — runs all stages in order | ⬜ | |
| `Jenkinsfile` (full, replaces Jenkinsfile.smoke) | ⬜ | |
| `.gitignore` — remove bin/, obj/, .vs/, .venv/ | ⬜ | Tracked generated files need cleanup |
| README updated to describe full workflow | ⬜ | |

---

## Phase 9 — Exploded Source Pipeline (Unblocked by Phase 6)

**Goal:** Replace the single L5X source file with exploded source controlled at the routine level.

Phase 6 confirmed `l5xplode` is available and the round-trip is semantically lossless. This phase can proceed.

| Task | Status | Notes |
|---|---|---|
| Commit exploded source to repo (`1-production-files/Source/`) | ⬜ | `l5xplode explode` output already tested locally. 47 files under `RSLogix5000Content/`. |
| Jenkins stage: implode `Source/` → `Build/Controller.L5X` | ⬜ | Wraps `l5xplode implode`. No interactive session needed (no SDK dependency). |
| Jenkins stage: build `Build/Controller.L5X` → `Build/Controller.ACD` | ⬜ | Uses `l5xgit acd2l5x` / Logix Designer SDK. Needs LD-SDK-Run path. |
| Add source validation stage before implode | ⬜ | Schema, required files, duplicate names |
| Update `Jenkinsfile` pipeline order | ⬜ | implode → build → deploy → test |
| Document source folder structure and authoring workflow | ⬜ | |

---

## Immediate Next Actions (This Session or Next)

Priority order:

1. **Run Echo smoke manually** on NIA-AUTO-ECH-01 (Phase 2 completion gate).
2. **Run Logix Designer open/close under Jenkins** — confirm `NT AUTHORITY\SYSTEM` can use the SDK (Phase 3 gate). This is the most uncertain step because the Logix Designer SDK may have interactive session requirements.
3. **Run code comparison manually** against HEAD and HEAD^ (Phase 3a gate).
4. **Investigate Echo controller lifecycle API** — enumerate what the Echo SDK exposes for create/download/run.
5. **Check VCS tooling** on NIA-AUTO-ECH-01 (Phase 6 investigation).

---

## Known Constraints and Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Logix Designer SDK requires interactive desktop session (affects Jenkins under Local System) | Medium | Test immediately — Phase 3 Jenkins gate. If blocked, SDK calls may need a scheduled task or service in user session context. |
| Echo SDK controller create/download API differs from documentation | Low | Investigate SDK before writing code. Use Echo read-only probe first. |
| VCS tooling not installed | Medium | Phase 8 remains L5X-based rather than exploded source. |
| Logix Designer v36 `build()` limitation | Confirmed | Do not use as Jenkins gate. Deferred to v37 upgrade. |
| `NT AUTHORITY\SYSTEM` cannot reach Echo service | Low | Phase 2 Jenkins run will confirm. Has not been tested yet. |
