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
| Manual validation on NIA-AUTO-ECH-01 | 🔶 | Not confirmed — run with HEAD and HEAD^ |
| Jenkins integration | 🔶 | Not yet added to Jenkinsfile.smoke |

**Next action:** Run `logix_code_compare.py` manually with `--base-revision HEAD^ --current-revision HEAD`. Confirm exit 0 and a valid comparison result before adding a Jenkins stage.

---

## Phase 4 — Echo Controller Lifecycle

**Goal:** Create an Echo controller, download a project to it, and put it in RUN mode programmatically.

This phase is the first that modifies Echo state. It must not run until Phase 2 is fully proven under Jenkins (Local System confirmed able to reach the Echo service).

| Task | Status | Notes |
|---|---|---|
| Investigate Echo SDK controller lifecycle API | ⬜ | Need to find: create_controller, download, set_mode |
| Controller naming convention | ⬜ | Proposed: `CICD_TEST_01` |
| `deploy.py` — create/reset/download/run | ⬜ | Must use `try/finally` for cleanup |
| Cleanup: stop and delete controller after each run | ⬜ | Leave no state on the Echo service |
| JSON artifact with controller state evidence | ⬜ | |
| Jenkins `rockwell-ci deploy` stage | ⬜ | |

**Design constraint:** The deploy stage must be idempotent. If a previous run left `CICD_TEST_01` behind, the script must detect and reset it rather than failing. Use a unique, unambiguous controller name that will never conflict with a real-use controller.

**Safety boundary:** The controller must be a disposable test instance. It must not be a controller used for any other purpose. It must be deleted or reset at the end of every pipeline run, in `try/finally`.

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

## Phase 6 — VCS Tooling Investigation (L5XExploder)

**Goal:** Determine whether Rockwell's VCS Custom Tools are installed and whether an implode operation (exploded source → L5X) is available.

This phase runs in parallel with Phases 4–5 and does not block them.

| Task | Status | Notes |
|---|---|---|
| Check whether L5XExploder is installed on NIA-AUTO-ECH-01 | ⬜ | Check PATH and `C:\Program Files\Rockwell Automation\` |
| Identify current tool name and version | ⬜ | Tool may have changed from older documentation |
| Confirm `implode` / `explode` command syntax | ⬜ | Do not assume — read actual help output |
| Explode ExampleForCICD_L85E.L5X into a test source folder | ⬜ | |
| Implode back and diff against original L5X | ⬜ | Confirm lossless round-trip |

**If VCS tools are not installed:** The single L5X remains the source of truth. The exploded source architecture (Phase 8) is deferred.

**If VCS tools are installed:** The planned directory structure becomes:

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

## Phase 9 — Exploded Source Pipeline (Conditional on Phase 6)

**Goal:** Replace the single L5X source file with exploded source controlled at the routine level.

This phase only proceeds if Phase 6 confirms VCS tooling is available and the round-trip is lossless.

| Task | Status | Notes |
|---|---|---|
| Add `rockwell-ci implode` — Source/ → Build/Controller.L5X | ⬜ | Wraps VCS tooling |
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
