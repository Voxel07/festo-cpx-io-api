# CPX-AP test platform implementation contract

This document describes the implemented architecture and the requirements that future changes must preserve. The platform dynamically tests remote I/O and valve products using `festo-cpx-io`, FastAPI, React/React Flow, PocketBase, and GitLab CI.

## Current implementation status

The action plan from the architecture review has been implemented:

- Hardware tests are planned, started, monitored, aborted, persisted, and reported through FastAPI.
- The standalone `test_runner.py`, pytest hardware orchestrator, `conftest.py`, and manual hardware suite were removed.
- There is no dry-run execution mode. `POST /test-run/plan` is the read-only API operation for resolving an execution plan.
- GitLab CI uses `ci_api_client.py` as an HTTP client. It does not import hardware drivers or test modules.
- Configuration is loaded through one JSONC-aware, typed validation path in `config_io.py` and `config_models.py`.
- Test assignment is capability-driven. Display-name glob compatibility tables are not an assignment mechanism.
- Each resolved hardware test runs in an isolated child process with its own `SafeSession`, deadline, abort handling, and emergency reset attempt.
- Safety opt-ins, a software emergency-stop latch, optional external interlock, bench serialization, and output/parameter audit events are enforced by the API.
- Result persistence is behind the repository service and includes runs, individual resolved results, plans, snapshots, structured events, and artifact references.
- Interactive hardware mutations return after the device operation; PocketBase audit writes are queued in a bounded background pool and reuse one authenticated HTTP session, so persistence outages do not stall controls.
- Connection-editor wiring checks use `POST /io/check-wiring`: the API serially pulses one source at a time, reads its target after a short settle interval, and resets every source in `finally`.
- The React topology uses stable module instance IDs for configured nodes and wiring edges. API-owned tests do not depend on the interactive UI hardware connection.

This is implementation verification, not a hardware qualification statement. Compilation, configuration loading, plan resolution, API route checks, frontend production builds, and React Doctor were exercised without a live CPX-AP bench. Hardware-in-the-loop behavior and PocketBase schema import still require the target environment.

## Topology
- The topology reader should read in the details of the currently connected system and give the option to sotre it to a file as well as compare the live config to an sotred on
- The config should incldde : - Position, Ordercode, Number of valves if its a valve terminal 

## Connections 
- There should be a tool to connect modules inputs and ouptus together the ruels are:
- M12 have 2 channels per connector M8 1 channel per connecotr
- If M12 is connected to m12 connect bot channels If m12 is connected to m8 then conly on channel can be connected. There should be a selector
- There should be a modal to select channel mode or port mode
- DI connectos to DO modules 
- DIDO modules can connect to bot and internaly. But only input to ouput not the same port
- DIO modules have ports that can be bot input and outpt its configurable per channel 
- DIO ports can connect to everything the port then has to be configured to be the opposit to of what it was connected to
- There are modules NDI or NDIO which are negative latching they can not be connected to normal DIO or DO DI modules.

## Non-negotiable architecture rules

1. FastAPI is the only hardware-test execution boundary.
2. Do not add a CLI/manual/pytest hardware runner or a second execution path.
3. Do not add a `dry-run` route or `DRY_RUN` switch. Resolve plans with `POST /test-run/plan`.
4. Generic tests must not select products by display name or product key. Use module, channel, mode, wiring, and safety capabilities. Product keys/module codes are allowed only as explicit config include/exclude overrides.
5. Bench-specific configuration may live in another repository/ref. Test code must remain reusable across benches.
6. Do not add frontend tests unless this contract is explicitly changed.
7. Keep PocketBase behind `ResultRepository`/`ResultStore`; do not couple test business logic to PocketBase HTTP calls.
8. Do not introduce PostgreSQL now. Keep identifiers, JSON fields, timestamps, and repository operations portable for a future adapter.

## Runtime flow

```text
React or GitLab CI
       |
       v
FastAPI /test-run/plan
       |
       v
JSONC loader -> Pydantic validation -> capability resolver -> stable plan_id
       |
       v
FastAPI /test-run/start (safety opt-ins + config/IP checks)
       |
       v
serial per-bench scheduler -> isolated child process per resolved instance
       |
       v
SafeSession -> festo-cpx-io -> safe teardown/emergency reset
       |
       v
ResultStore -> PocketBase collections + API status/SSE/JUnit
```

## Canonical API contract

The primary test endpoints are:

- `POST /test-run/plan` — validate config and return the resolved, hardware-free plan.
- `POST /test-run/start` — validate the same configuration, safety policy, requested tests, and IP; start API-owned execution.
- `GET /test-run/status` — current status, progress, results, checkpoints, and logs.
- `POST /test-run/abort` — stop the active child process and attempt safe hardware recovery.
- `GET /test-run/{run_id}` — persisted/current run detail.
- `GET /test-run/{run_id}/junit.xml` — JUnit generated from API results.
- `GET /safety/status` — software E-stop and external interlock state.
- `POST /safety/emergency-stop` — latch E-stop, abort execution, stop automation, and reset outputs.
- `POST /safety/reset` — explicitly clear the software latch after the physical system is safe.
- `POST /io/check-wiring` — safely validate multiple configured wires in one API round trip while retaining serial electrical isolation.

`StartTestRunRequest` requires explicit `allow_destructive` and `allow_negative` opt-ins when applicable and supports a per-test timeout. The requested IP must match the selected bench configuration.

## Configuration contract

`config_io.load_bench_config()` is the canonical loader. It supports JSONC comments and trailing commas without corrupting strings such as `http://...`.

`BenchConfig` contains:

- Test-bench identity, version, schema version, description, and IP.
- Module instances with stable instance ID, module code, product key, bus address, type reference, authoritative `capabilities`, firmware, serial number, presence state, I/O counts, valve state, and negative-test marker.
- Module types with product family, legacy/default capabilities, channel definitions, channel modes, limits, image asset, valve count, `channels_per_valve`, and product-specific `test_parameters`.
- Wiring with stable ID, instance/channel endpoints, signal type, direction, expected behavior, and physical/simulated/virtual type.
- Test definitions with stable ID/version, assignment scope (`system`, `module`, `channel`, `wiring`), required capabilities/modes/wiring, category and include/exclude rules, safety class, CI policy, parallel declaration, target instances, and parameters.
- UI positions, channel anchors, hotspot geometry, and rendering metadata.

Validation rejects invalid JSONC, missing/invalid fields, duplicate instance IDs/addresses/product keys, invalid type references, duplicate or ambiguous wiring endpoints, self-wiring, invalid direction, unknown modules/channels/capabilities/target modules, invalid modes, invalid UI references/geometry, and missing configured assets.

`module_instances[].capabilities` is authoritative when present, including an explicitly empty list. Type capabilities are inherited only when that field is omitted for backward compatibility. Specialized behavior such as `condition_counter` must be declared on the concrete supporting products and must never be inferred from the broad input/output/inout category. Valve counters use the distinct `valve_condition_counter` capability.

Legacy configs without `module_types` are upgraded conservatively from explicit category and I/O counts. Newly generated configs must persist concrete module capabilities; product behavior must not be inferred from a display name.

## Resolver contract

`TestResolver` produces concrete `ResolvedTestInstance` objects and supports:

- System, module, channel, and wiring assignment.
- Required concrete-module/channel capabilities and supported/current channel modes.
- Wiring type and stable wiring ID binding.
- Module code/product key include/exclude filters.
- Target module instance filters.
- Negative expected-failure targets.
- Test, module, module-code, product-key, capability, and safety filters.

The plan contains a deterministic `plan_id`, creation timestamp, stable resolved IDs, safety metadata, parameters, module/channel/wiring bindings, and `execution_policy: serial_per_bench`. A test may declare `can_run_parallel`, but a single physical bench remains serial because concurrent Modbus clients and output-changing tests are unsafe.

## Test implementation contract

Files under `tests/` are runtime test plugins plus ordinary software unit tests; the directory name does not authorize direct hardware execution with pytest.

Runtime plugins:

- Declare `TEST_DEFINITION`/`TEST_DEFINITIONS` using capabilities.
- Expose a `run()` function invoked only by the API dispatcher in an isolated process.
- Receive the validated `BenchConfig` and resolved module address/channel/wiring context.
- Access hardware through `HardwareInterface`/`CpxApHardware` and `SafeSession`.
- Restore outputs and configurable directions on success, failure, timeout, communication loss, or abort.
- Return structured results rather than persisting directly.

Product-specific valve geometry and diagnostic parameters belong to `module_types`, not duplicated Python/TypeScript display-name maps.

## Safety contract

- The scheduler is serialized per bench and GitLab uses `resource_group` locking.
- Output and parameter mutations are blocked while a test run is active.
- Output activation checks the E-stop/interlock, configured voltage limits, and wiring direction.
- Destructive and negative tests require separate explicit opt-ins.
- Every child has a hard deadline and is terminated/killed if necessary.
- Abort and timeout trigger a fresh safe-session reset attempt.
- `FESTO_INTERLOCK_URL`, when configured, is fail-closed and is polled during a running child.
- Output-changing and parameter-changing actions emit structured audit events.
- The config remains the authority for electrical/current/pressure limits; physical protection must also exist on the bench.

## Persistence contract

The repository layer stores or exposes operations for:

- `festo_test_runs`
- `festo_test_results`
- `festo_system_logs`
- `festo_resolved_plans`
- `festo_module_snapshots`
- `festo_wiring_snapshots`
- `festo_artifacts`
- automation programs and run history

Records use stable run/test/resolved/module/channel/wiring identifiers, UTC timestamps, duration, verdict, failure/exception data, measurements, commit/pipeline metadata, and artifact/log references. JSON is stored as structured JSON, not double-encoded strings. Startup reconciliation marks stale running records interrupted.

PocketBase authentication tokens and HTTP connections are reused. Action audit events are best-effort background writes; action success is determined by the hardware/API result, not PocketBase response latency. Read endpoints such as history and dashboard still await repository reads because their response data comes from persistence.

## GitLab CI contract

`.gitlab-ci.yml` starts the API and calls it through `ci_api_client.py` for validation, planning, execution, polling, result export, and JUnit download. Hardware modules are never imported by the CI client.

Supported inputs include:

- `CONFIG_REPO_URL`, `CONFIG_REPO_PATH`, and `CONFIG_REF`
- `CONFIG_PATH` and `CONFIG_COMMIT`
- `TESTBENCH_ID`
- `POCKETBASE_URL`, `POCKETBASE_TOKEN`, or PocketBase credentials
- `GITLAB_PIPELINE_ID`, `GITLAB_JOB_ID`, and commit SHA
- `TEST_FILTER` and `SAFETY_CLASS_FILTER`
- destructive/negative opt-ins and per-test timeout

CI must retain the resolved plan, result JSON, JUnit XML, and API log as artifacts. Do not reintroduce pytest as the hardware execution command.

## React/React Flow contract

- Load canonical config through the API.
- Use module instance IDs and channel/wiring IDs as React Flow identities.
- Render configured module images, anchors/hotspots, wiring, modes, metadata, test status, history, logs, and diagnoses.
- Keep backend persistence out of component state.
- Use configured valve geometry rather than display-name compatibility tables.
- Avoid duplicate polling/subscriptions, unstable default arrays, array scans inside render loops, uncleaned timers, and render-time impure/ref access.
- Lazy-load large feature areas and keep large vendor dependencies split where practical.

React Doctor verification after the execution/topology changes reports no errors for changed files and a score of 71/100; the remaining 13 changed-file findings are warnings only. The full legacy project scan still identifies broader work in `AutomationStudio`, large components, effect-based data fetching, accessibility, and bundle size. These are not hardware execution blockers, but should be handled incrementally.

## Verification checklist for future changes

Before merging:

1. Compile the Python tree and import `api`.
2. Load each representative JSONC config through `config_io`.
3. Resolve the same config twice and verify stable plan IDs.
4. Verify `/test-run/plan` exists and `/test-run/dry-run` does not.
5. Confirm no source references to `test_runner`, `test_suite`, `DRY_RUN`, or the removed PocketBase logger.
6. Build the React production bundle.
7. Run React Doctor with `--scope changed`; address errors and document justified warnings.
8. Validate `pocketbase_schema.json` and import it in a disposable PocketBase instance.
9. On a protected bench, exercise plan/start/status/abort/JUnit, timeout recovery, E-stop, external interlock, and persistence.

Do not claim full hardware readiness until checklist items 8 and 9 have been completed in the target environment.
