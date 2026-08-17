# Architecture — Rockwell CI/CD Lab

**Last updated:** 2026-08-17  
**Branch:** `lab`  
**Machine:** NIA-AUTO-ECH-01 (Windows Server 2022 Datacenter)

---

## Purpose

This lab proves that a Logix controller project can be treated as software: source-controlled, automatically validated, and deployed to a simulated environment on every Git push — without manual Studio 5000 interaction after the initial commit.

The deliverable is not a Jenkins pipeline. It is a Python CLI tool (`rockwell-ci`) that performs every Rockwell-specific operation. Jenkins, GitHub Actions, or a developer terminal all call the same tool. The CI orchestrator is interchangeable.

---

## Proven Environment

| Component | Version / Detail |
|---|---|
| OS | Windows Server 2022 Datacenter |
| Studio 5000 Logix Designer | 36.04 |
| FactoryTalk Logix Echo | 4.0 |
| Jenkins | 2.568.x (service account: `NT AUTHORITY\SYSTEM`) |
| Java | Eclipse Temurin JDK 21 |
| .NET SDKs | 9.0.315 and 10.0.301 |
| Python (Echo SDK) | 3.12 x64 — `C:\Program Files\Python312\python.exe` |
| Python (Logix Designer SDK) | 3.13 x64 — `C:\Program Files\Python313\python.exe` |
| Git | Installed, accessible to Local System |

---

## Rockwell SDK Locations

| SDK | Path |
|---|---|
| Echo SDK wheel | `C:\Users\Public\Documents\FactoryTalk Logix Echo\SDK\Python\ftecho_sdk-4.0.0-py3-none-any.whl` |
| Logix Designer SDK wheel | `C:\Users\Public\Documents\Studio 5000\Logix Designer SDK\python\logix_designer_sdk-2.0.2-py3-none-any.whl` |
| Echo .NET package | `C:\Users\Public\Documents\FactoryTalk Logix Echo\SDK\dotnet\RockwellAutomation.FactoryTalkLogixEcho.Api.Client.4.0.1437.nupkg` |
| Logix Designer .NET package | `C:\Users\Public\Documents\Studio 5000\Logix Designer SDK\dotnet\RockwellAutomation.LogixDesigner.CSClient.2.2.1109.nupkg` |
| Local NuGet feed | `C:\data\nuget\rockwell` |

---

## Architecture Decisions

### ADR-001 — Python-first SDK integration

Both the Echo SDK and Logix Designer SDK now have official Python support. The implementation uses Python as the primary automation layer. C# remains available as a fallback if a specific SDK operation proves inaccessible from Python, but no new C# wrapper executables will be built.

Rejected: writing C# wrapper executables for all SDK calls. Retained: the existing C# `LogixUnitTesting.sln` as a behavioral reference until Python parity is established.

### ADR-002 — Two separate virtual environments

The Echo SDK (`ftecho_sdk` 4.0.0) supports Python 3.12 and 3.13. The Logix Designer SDK (`logix-designer-sdk` 2.0.2) has been validated on Python 3.13, with `numpy` as an additional dependency that conflicts with Echo's dependency set. Two repository-owned venvs are maintained:

| venv | Python | Purpose |
|---|---|---|
| `.venv` | 3.12 x64 | Echo SDK — Echo connectivity, controller lifecycle, tag I/O |
| `.venv-logix-designer` | 3.13 x64 | Logix Designer SDK — ACD open/save/export, code compare |

Both venvs are installed from local wheels and pinned lock files. Neither depends on internet access or per-user packages.

### ADR-003 — Source of truth and build artifacts

`1-production-files/L5Xs/` holds the L5X project that engineers commit to Git. The ACD (`1-production-files/ACDs/`) is a build artifact derived from the L5X. Neither file should be hand-edited outside of Studio 5000.

The long-term direction is an exploded source representation (using Rockwell's VCS Custom Tools) where individual routines, tags, and data types are committed as separate XML files. Until VCS tooling is confirmed on this machine, the single L5X remains the source of truth.

### ADR-004 — `rockwell-ci` CLI is the product; Jenkins is the caller

Jenkins does not contain Rockwell logic. The Jenkinsfile calls the Python CLI and reads exit codes and JSON artifacts. This makes the pipeline portable to GitHub Actions, Azure DevOps, or a developer terminal without changing the implementation.

Target CLI surface:

```
rockwell-ci validate     # XML/schema validation of source before touching Studio 5000
rockwell-ci build        # L5X → ACD using Logix Designer SDK
rockwell-ci deploy       # ACD → Echo controller (create, download, run)
rockwell-ci test         # Execute YAML test cases, emit JUnit XML
rockwell-ci compare      # Compare routine exports between two Git commits
rockwell-ci pipeline     # Run all stages in sequence
```

### ADR-005 — Incremental gating, nothing touches controllers until proven

Each phase gate must pass before the next phase begins. The sequence of trust is:

1. Infrastructure (Jenkins, .NET build) ✓
2. SDK import and read-only Echo connectivity ✓  
3. SDK import and read-only Logix Designer operations ✓
4. Git-aware routine code comparison ✓
5. Echo controller lifecycle (create, download, run)
6. Tag reads/writes against a running simulated controller
7. YAML-driven automated test cases
8. Full `rockwell-ci pipeline` end-to-end

### ADR-006 — Logix Designer `build()` requires v37+

The Logix Designer SDK `LogixProject.build()` method is not supported on v36.04. The call throws `OperationFailedError`. The Jenkins parameter `RUN_PYTHON_LOGIX_DESIGNER_EXECUTABLE_ENUMERATION` must not be used as a build gate until the machine is upgraded to Logix Designer v37 or later. The `--build-validation` flag is documented and available for future use.

### ADR-007 — Jenkins NuGet.Config at repository root

`NT AUTHORITY\SYSTEM` cannot see per-user NuGet package sources. A `NuGet.Config` committed at the repository root clears all other sources and adds only `nuget.org` and the local Rockwell feed at `C:\data\nuget\rockwell`. Jenkins passes `--configfile "%WORKSPACE%\NuGet.Config"` explicitly to every `dotnet restore` call.

### ADR-008 — YAML test case format

PLC test cases are defined in YAML. The Python test framework reads them. Excel is output-only — if Excel reports are eventually needed, they are generated from the Python result model, not used as input. This separates test authoring from result formatting.

Example test case format (target, not yet implemented):

```yaml
controller: CICD_TEST_01
timeout_seconds: 5.0
setup:
  EStop_OK: true
  MotorFault: false
  StartPB: false
steps:
  - name: Motor starts when permissives met
    write:
      StartPB: true
    expect:
      Conveyor_RunCmd: true
    timeout_ms: 500
```

### ADR-009 — Disposable project copies, never open source files

Every Logix Designer SDK operation opens a temporary copy of the source project, not the source file itself. The script:

1. Calculates the source SHA-256 before opening.
2. Copies to a temp directory.
3. Verifies the copy SHA-256.
4. Opens only the copy.
5. Verifies the source SHA-256 is unchanged after close.
6. Removes the temp directory.

This is enforced by `logix_designer_executables.py` and `logix_code_compare.py`. Any future Logix Designer operation must follow the same pattern.

### ADR-010 — Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Test failure (tests ran, assertion failed) |
| 2 | Configuration or input validation error |
| 3 | SDK import or dependency error |
| 4 | Rockwell service unavailable or SDK error |
| 5 | Cleanup failure (temp files or controller state not restored) |

---

## Target Pipeline

```
Developer modifies L5X (or exploded source, future)
        ↓
git commit + push
        ↓
Jenkins detects change
        ↓
rockwell-ci validate
        ↓  (fail fast before touching SDKs)
rockwell-ci build      [Logix Designer SDK, .venv-logix-designer]
        ↓
Build/Controller.ACD
        ↓
rockwell-ci compare    [Logix Designer SDK, compares HEAD^ to HEAD]
        ↓
rockwell-ci deploy     [Echo SDK, .venv]
        ↓
Echo: CICD_TEST_01 running
        ↓
rockwell-ci test       [Echo SDK, YAML test cases → JUnit XML]
        ↓
PASS / FAIL
Jenkins publishes JUnit test results
Jenkins archives JSON artifacts
```

---

## Repository Layout (current + target)

```
ra-logix-cicd/
├── Jenkinsfile.smoke          # Incremental smoke pipeline (current CI entry point)
├── NuGet.Config               # Repository-level NuGet source configuration
│
├── 1-production-files/
│   ├── ACDs/
│   │   └── ExampleForCICD_L85E.ACD    # Build artifact (also serves as reference)
│   └── L5Xs/
│       ├── ExampleForCICD_L85E.L5X    # Source of truth (single-file, current)
│       ├── DelayedSum_AOI.L5X
│       └── WetBulbTemperature.L5X
│
├── 2-cicd-config/             # Original Rockwell example — preserved as reference
│
├── python/
│   ├── README.md
│   ├── LOGIX_DESIGNER_SMOKE.md
│   ├── LOGIX_CODE_COMPARE.md
│   ├── common/                # Shared: exit_codes, logging_config, result helpers
│   ├── compare/               # logix_code_compare.py — Git-aware routine diff
│   ├── smoke/                 # echo_connectivity.py, logix_designer_executables.py
│   ├── requirements/          # echo-sdk.lock.txt, logix-designer-sdk.lock.txt
│   ├── scripts/               # setup_venv.ps1, setup_logix_designer_venv.ps1
│   └── tests/                 # Unit tests (no Rockwell hardware required)
│
├── docs/
│   ├── ARCHITECTURE.md        # This file
│   └── IMPLEMENTATION_PLAN.md # Phased plan and current status
│
└── [future]
    ├── rockwell_ci/           # Python package: cli.py, build.py, deploy.py, test.py
    ├── test_cases/            # YAML test case definitions
    └── Jenkinsfile            # Full pipeline (replaces Jenkinsfile.smoke)
```

---

## Sample Project

**Controller:** `ExampleForCICD_L85E` (1756-L85E, ControlLogix)  
**Studio 5000 version:** 36.04  
**Communications path (ACD):** `EmulateEthernet\127.0.0.1`  
**AOIs:** `DelayedSum`, `WetBulbTemperature`  

This is the test subject for all pipeline stages until a domain-specific project is chosen.

---

## Open Questions

| Question | Impact | Status |
|---|---|---|
| Is Rockwell VCS Custom Tools (L5XExploder) installed on NIA-AUTO-ECH-01? | Determines whether exploded source workflow is possible | Unknown — needs verification |
| Can `NT AUTHORITY\SYSTEM` communicate with the Echo service? | Blocks Jenkins Echo stage | Unproven — Python smoke must run under Jenkins |
| Does the Logix Designer SDK support ACD export from L5X via `save()`? | Required for `rockwell-ci build` | Needs investigation |
| What is the Echo API for controller lifecycle (create, download, run)? | Required for `rockwell-ci deploy` | Needs investigation |
| Upgrade path to Logix Designer v37? | Unlocks `build()` and removes the ACD pre-generation workaround | Not scheduled |
