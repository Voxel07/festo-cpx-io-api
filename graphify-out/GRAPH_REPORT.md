# Graph Report - festo-cpx-io-api  (2026-07-15)

## Corpus Check
- 39 files · ~43,840 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 741 nodes · 1833 edges · 34 communities (31 shown, 3 thin omitted)
- Extraction: 84% EXTRACTED · 16% INFERRED · 0% AMBIGUOUS · INFERRED: 300 edges (avg confidence: 0.56)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `d24ee03d`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 45|Community 45]]

## God Nodes (most connected - your core abstractions)
1. `HardwareInterface` - 82 edges
2. `BenchConfig` - 76 edges
3. `CpxApHardware` - 50 edges
4. `FakeHardware` - 36 edges
5. `AutomationProgram` - 31 edges
6. `SimulatedHardware` - 31 edges
7. `TestResolver` - 31 edges
8. `load_bench_config()` - 30 edges
9. `AutomationEngine` - 29 edges
10. `ModuleInfo` - 28 edges

## Surprising Connections (you probably didn't know these)
- `HwConnectRequest` --uses--> `BenchConfig`  [INFERRED]
  api.py → config_models.py
- `HwConnectRequest` --uses--> `CpxApHardware`  [INFERRED]
  api.py → hal.py
- `save_automation_program()` --references--> `AutomationProgram`  [EXTRACTED]
  api.py → automation.py
- `update_automation_program()` --references--> `AutomationProgram`  [EXTRACTED]
  api.py → automation.py
- `ConfigGenerateRequest` --uses--> `BenchConfig`  [INFERRED]
  api.py → config_models.py

## Import Cycles
- 1-file cycle: `tests/__init__.py -> tests/__init__.py`

## Communities (34 total, 3 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.17
Nodes (10): MeasurementRecord, pocketbase_api_context(), Database abstraction layer (Repository pattern).  Defines :class:`ResultReposi, A single test execution run., Persist the plan and immutable module/wiring snapshots for a run., Return current UTC time as ISO 8601 string., Return the shared PocketBase endpoint and authenticated headers., A single measurement taken during a test. (+2 more)

### Community 1 - "Community 1"
Cohesion: 0.05
Nodes (32): BaseException, CpxApHardware, Disconnect and reconnect.  Convenience wrapper around         :meth:`disconnect, Set a configurable channel direction and register it for cleanup., Restore all configurable channels touched by this session to input., Production implementation wrapping the ``festo-cpx-io`` library., Read a module process image once and select the requested channels.          ``A, Read an analog module process image once and preserve numeric values. (+24 more)

### Community 2 - "Community 2"
Cohesion: 0.20
Nodes (10): io_set_all_outputs(), _queue_audit(), Persist an audit event without adding PocketBase latency to an action., Latch the emergency stop, abort tests, and reset live connections., Write a new value to a module parameter and read it back., Set all writable output/inout channels of a module HIGH or LOW.      When *chan, safety_emergency_stop(), write_module_parameter() (+2 more)

### Community 3 - "Community 3"
Cohesion: 0.14
Nodes (30): LogFn, PowerCycleSession, Context manager for HMP40x0 power-cycle operations.      Opens the serial/IP c, _get_reset_param_specs_for_module(), _module_family_key(), _power_cycle_reconnect(), Factory Reset and Normal Reset parameter persistence test.  Ported from the sm, Return the configured device-family key matching ``module_name``.      Matchin (+22 more)

### Community 4 - "Community 4"
Cohesion: 0.09
Nodes (12): HardwareInterface, Force all output channels on all modules to a safe (LOW) state., Write a parameter value to a module., Check whether individual channel writes are supported., Trigger a device reset via a parameter write.          After the write the mod, Abstract interface for all hardware interactions.      All test code MUST use, Establish a connection to the hardware., Gracefully close the connection. (+4 more)

### Community 5 - "Community 5"
Cohesion: 0.04
Nodes (40): architecture_graph(), automation_programs(), ci_environment(), clear_test_run_history(), dashboard_data(), delete_test_run(), pocketbase_health(), FastAPI backend for the CPX-AP Topology Manager.  Development workflow ------ (+32 more)

### Community 6 - "Community 6"
Cohesion: 0.14
Nodes (25): Dispatch a single test using a pre-connected HardwareInterface.      Each test, _run_single_test_hw(), callable, BenchConfig, Complete test bench configuration., channel_index_from_port(), load_bench_config(), noop_log() (+17 more)

### Community 7 - "Community 7"
Cohesion: 0.05
Nodes (91): ConfigCompareRequest, ConfigGenerateRequest, ConfigSavePayload, HwConnectRequest, plan_test_run(), PlanTestRunRequest, Request body for API-owned execution planning., Validate configuration and return the exact API execution plan. (+83 more)

### Community 8 - "Community 8"
Cohesion: 0.13
Nodes (15): _atomic_write_text(), compare_config(), _config_roots(), delete_config(), load_config(), Resolve a client path inside an allowed configuration root., Start a test run.  Returns 409 if another run is already in progress., Load a previously saved unified BenchConfig file. (+7 more)

### Community 9 - "Community 9"
Cohesion: 0.07
Nodes (20): main(), plan(), _plan_payload(), Thin GitLab client for the FastAPI test service.  This module never imports hard, _request(), run(), _wait_for_api(), ConnectionManager (+12 more)

### Community 10 - "Community 10"
Cohesion: 0.09
Nodes (25): _execute_test_run_safe(), _extract_error_summary(), get_module_metadata(), _merge_sub_results(), Build a human-readable error string from a result dict.      Handles nested ``, Merge *incoming* sub-results into *existing* by address/module key.      Repla, Execute an API-resolved plan with process-isolated safety timeouts.      Runs i, Return the SVG icon file mapping (OrderCode -> filename). (+17 more)

### Community 11 - "Community 11"
Cohesion: 0.10
Nodes (19): get_module_parameters(), get_system_diagnoses(), hw_connect(), hw_disconnect(), hw_status(), io_read_all(), Manually establish the shared Modbus connection to the CPX-AP gateway.      Di, Retrieve metadata for all parameters available on the module at the given addres (+11 more)

### Community 12 - "Community 12"
Cohesion: 0.11
Nodes (25): ApModule, CpxAp, _channel_index_from_port(), compare_topology(), _find_module_by_addr(), generate_topology(), _module_series(), module_to_topology_entry() (+17 more)

### Community 13 - "Community 13"
Cohesion: 0.05
Nodes (10): AutomationEngine, AutomationProgramStore, _incoming_values(), In-memory HAL used to run the exact automation engine without CPX hardware., Single-program cyclic executor for the shared Modbus connection., PocketBase-backed program CRUD with a clearly marked memory fallback., SimulatedHardware, AutomationEngineTests (+2 more)

### Community 14 - "Community 14"
Cohesion: 0.18
Nodes (4): Any, Read a parameter value from a module., Read diagnosis information from a module., Update the status of an existing test run.

### Community 15 - "Community 15"
Cohesion: 0.32
Nodes (6): Test registry for the CPX-AP validation suite.  Each entry maps a test ID (as, Connection wiring validation test.  Pulses each source output defined in *conn, Validate all I/O connections listed in *connections_path*.      Accepts either, Test one I/O connection by pulsing the source output and reading the target inpu, run(), validate_single()

### Community 16 - "Community 16"
Cohesion: 0.29
Nodes (6): _get_mounted_valves(), Valve Terminal Condition Counter test.  Uses :class:`hal.HardwareInterface` —, Return ``{module_address: [mounted_valve_slot, ...]}`` from config., Test CC behaviour on VABX valve terminals.      Steps (per valve terminal):, run(), Valve slot/channel arithmetic.  Product-specific channel counts live in ``BenchC

### Community 17 - "Community 17"
Cohesion: 0.33
Nodes (6): io_check_wiring(), io_read_input(), io_set_output(), Pulse and verify configured wires using one API request.      Connections remain, Set a single output channel on a module HIGH or LOW.      Uses the shared hardw, Read one or more input channels from a module (all channels of an M12 connector)

### Community 18 - "Community 18"
Cohesion: 0.50
Nodes (4): _enrich_generated_metadata(), generate_config(), Fix generated module metadata that cannot be inferred reliably from live topolog, Query live hardware to discover modules and generate a modern BenchConfig struct

### Community 19 - "Community 19"
Cohesion: 0.22
Nodes (7): ABC, Hardware Abstraction Layer for the CPX-AP test framework.  Defines :class:`Har, is_available(), PowerSupplyNotAvailable, Power supply control for hardware-in-the-loop tests.  Wraps the HMP40x0 serial, Raised when the HMP40x0 library cannot be imported., Return ``True`` if the HMP40x0 driver was loaded successfully or IP address is u

### Community 20 - "Community 20"
Cohesion: 0.15
Nodes (7): Abstract interface for persisting test results., Create a new test run record., Record a single test result., Record a measurement., Retrieve recent test runs., Get full detail for a specific run., ResultRepository

### Community 21 - "Community 21"
Cohesion: 0.10
Nodes (7): Reject impossible capability and explicit module references., Product keys identify physical products and must be unique per bench., Verify wiring references valid source and target channels., _is_ip_address(), ValueError, Return hardware channel indices for one zero-based valve slot., valve_slot_to_channels()

### Community 22 - "Community 22"
Cohesion: 0.18
Nodes (10): 1 – Install the CPX-IO library, 2 – Install this project's dependencies, 3 – Start the API, 4 – Frontend (development), 4 – Frontend (production), API Stuff, cpx-ap-topology-manager, Repository layout (+2 more)

### Community 23 - "Community 23"
Cohesion: 0.22
Nodes (10): _auto_reset_output(), _has_dio_direction_parameter(), _is_configurable_output(), _output_channel_indices(), Background callback that resets an output to LOW., Map a UI port to indices in ``mod.channels.outputs``., Return whether the module exposes configurable direction parameter 20145., Return whether an output-process-image channel is configurable DIO. (+2 more)

### Community 25 - "Community 25"
Cohesion: 0.50
Nodes (5): Yield SSE frames for *run_id*, starting with any buffered log entries., Server-Sent Events stream of log entries for *run_id*.      Connect with ``Eve, _sse_generator(), stream_run_logs(), Request

### Community 28 - "Community 28"
Cohesion: 0.67
Nodes (3): Parameter, make_param(), Construct (or reuse) a minimal Parameter object.      Cached per (parameter_id

### Community 33 - "Community 33"
Cohesion: 0.31
Nodes (3): PocketBaseRepository, PocketBase-backed repository.      Collections used (must match ``pocketbase_s, Mark abandoned running records as interrupted after API restarts.

### Community 35 - "Community 35"
Cohesion: 0.14
Nodes (13): Canonical API contract, Configuration contract, CPX-AP test platform implementation contract, Current implementation status, GitLab CI contract, Non-negotiable architecture rules, Persistence contract, React/React Flow contract (+5 more)

### Community 41 - "Community 41"
Cohesion: 0.40
Nodes (3): LogEventRecord, Record a structured log event., Structured log event.

### Community 42 - "Community 42"
Cohesion: 0.29
Nodes (6): Resolve and orient wiring suitable for condition-counter tests.          CC para, Condition-counter validation for wired CPX-AP-I-16* channels.  The resolver owns, Run CC edge validation, then verify successful counters persist., Count rising edges on every resolved route for one CC module., run(), run_with_power_cycle()

### Community 45 - "Community 45"
Cohesion: 0.13
Nodes (21): _diag_locations_text(), _diagnosis_id_from_result(), _expected_channels_from_mounted_valves(), _expected_diag_name(), _format_diag_id(), _get_module_config(), _mounted_valves_from_bench_config(), _mounted_valves_to_text() (+13 more)

## Knowledge Gaps
- **21 isolated node(s):** `cpx-ap-topology-manager`, `Repository layout`, `API Stuff`, `1 – Install the CPX-IO library`, `2 – Install this project's dependencies` (+16 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **3 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `HardwareInterface` connect `Community 4` to `Community 1`, `Community 3`, `Community 6`, `Community 7`, `Community 9`, `Community 42`, `Community 11`, `Community 13`, `Community 14`, `Community 45`, `Community 15`, `Community 16`, `Community 19`?**
  _High betweenness centrality (0.221) - this node is a cross-community bridge._
- **Why does `CpxApHardware` connect `Community 1` to `Community 4`, `Community 5`, `Community 7`, `Community 9`, `Community 11`, `Community 13`, `Community 15`, `Community 19`?**
  _High betweenness centrality (0.103) - this node is a cross-community bridge._
- **Why does `BenchConfig` connect `Community 6` to `Community 3`, `Community 5`, `Community 7`, `Community 10`, `Community 42`, `Community 45`, `Community 15`, `Community 16`, `Community 18`, `Community 21`?**
  _High betweenness centrality (0.102) - this node is a cross-community bridge._
- **Are the 16 inferred relationships involving `HardwareInterface` (e.g. with `AutomationEdge` and `AutomationEngine`) actually correct?**
  _`HardwareInterface` has 16 INFERRED edges - model-reasoned connections that need verification._
- **Are the 17 inferred relationships involving `BenchConfig` (e.g. with `ConfigCompareRequest` and `ConfigGenerateRequest`) actually correct?**
  _`BenchConfig` has 17 INFERRED edges - model-reasoned connections that need verification._
- **Are the 16 inferred relationships involving `CpxApHardware` (e.g. with `ConfigCompareRequest` and `ConfigGenerateRequest`) actually correct?**
  _`CpxApHardware` has 16 INFERRED edges - model-reasoned connections that need verification._
- **Are the 7 inferred relationships involving `FakeHardware` (e.g. with `AutomationEngine` and `AutomationProgram`) actually correct?**
  _`FakeHardware` has 7 INFERRED edges - model-reasoned connections that need verification._