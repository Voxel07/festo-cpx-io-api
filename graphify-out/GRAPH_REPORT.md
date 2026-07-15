# Graph Report - festo-cpx-io-api  (2026-07-15)

## Corpus Check
- 40 files · ~44,259 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 747 nodes · 1868 edges · 38 communities (33 shown, 5 thin omitted)
- Extraction: 84% EXTRACTED · 16% INFERRED · 0% AMBIGUOUS · INFERRED: 307 edges (avg confidence: 0.55)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `0f0d9c4b`
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
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
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
2. `BenchConfig` - 81 edges
3. `CpxApHardware` - 50 edges
4. `FakeHardware` - 36 edges
5. `TestResolver` - 34 edges
6. `AutomationProgram` - 31 edges
7. `SimulatedHardware` - 31 edges
8. `load_bench_config()` - 30 edges
9. `AutomationEngine` - 29 edges
10. `TestDefinition` - 29 edges

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

## Communities (38 total, 5 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.17
Nodes (10): MeasurementRecord, pocketbase_api_context(), Database abstraction layer (Repository pattern).  Defines :class:`ResultReposi, A single test execution run., Persist the plan and immutable module/wiring snapshots for a run., Return current UTC time as ISO 8601 string., Return the shared PocketBase endpoint and authenticated headers., A single measurement taken during a test. (+2 more)

### Community 1 - "Community 1"
Cohesion: 0.06
Nodes (23): BaseException, CpxApHardware, Disconnect and reconnect.  Convenience wrapper around         :meth:`disconnect, Set a configurable channel direction and register it for cleanup., Restore all configurable channels touched by this session to input., Production implementation wrapping the ``festo-cpx-io`` library., Read a module process image once and select the requested channels.          ``A, Read an analog module process image once and preserve numeric values. (+15 more)

### Community 2 - "Community 2"
Cohesion: 0.10
Nodes (13): AutomationEdge, AutomationNode, AutomationPosition, AutomationProgramStore, _execution_order(), Scratch-style automation graph execution and PocketBase persistence.  The brow, Return a stable topological order, tolerating feedback around state blocks., PocketBase-backed program CRUD with a clearly marked memory fallback. (+5 more)

### Community 3 - "Community 3"
Cohesion: 0.14
Nodes (30): LogFn, PowerCycleSession, Context manager for HMP40x0 power-cycle operations.      Opens the serial/IP c, _get_reset_param_specs_for_module(), _module_family_key(), _power_cycle_reconnect(), Factory Reset and Normal Reset parameter persistence test.  Ported from the sm, Return the configured device-family key matching ``module_name``.      Matchin (+22 more)

### Community 4 - "Community 4"
Cohesion: 0.07
Nodes (19): HardwareInterface, Force all output channels on all modules to a safe (LOW) state., Write a parameter value to a module., Check whether individual channel writes are supported., Trigger a device reset via a parameter write.          After the write the mod, Abstract interface for all hardware interactions.      All test code MUST use, Establish a connection to the hardware., Gracefully close the connection. (+11 more)

### Community 5 - "Community 5"
Cohesion: 0.04
Nodes (46): architecture_graph(), automation_programs(), ci_environment(), clear_test_run_history(), dashboard_data(), delete_test_run(), _execute_test_run_safe(), _extract_error_summary() (+38 more)

### Community 6 - "Community 6"
Cohesion: 0.14
Nodes (24): ABC, callable, BenchConfig, Complete test bench configuration., Hardware Abstraction Layer for the CPX-AP test framework.  Defines :class:`Har, channel_index_from_port(), load_bench_config(), noop_log() (+16 more)

### Community 7 - "Community 7"
Cohesion: 0.26
Nodes (35): ConfigCompareRequest, ConfigGenerateRequest, ConfigSavePayload, HwConnectRequest, PlanTestRunRequest, Request body for API-owned execution planning., SetAllOutputsRequest, SetOutputRequest (+27 more)

### Community 8 - "Community 8"
Cohesion: 0.10
Nodes (23): _atomic_write_text(), compare_config(), _config_roots(), delete_config(), _enrich_generated_metadata(), generate_config(), load_config(), plan_test_run() (+15 more)

### Community 9 - "Community 9"
Cohesion: 0.06
Nodes (30): get_module_metadata(), Return the SVG icon file mapping (OrderCode -> filename)., Return the contents of module_metadata.json, svg_map(), main(), plan(), _plan_payload(), Thin GitLab client for the FastAPI test service.  This module never imports hard (+22 more)

### Community 10 - "Community 10"
Cohesion: 0.21
Nodes (4): AutomationEngine, Single-program cyclic executor for the shared Modbus connection., AutomationEngineTests, program()

### Community 11 - "Community 11"
Cohesion: 0.07
Nodes (32): get_module_parameters(), get_system_diagnoses(), hw_connect(), hw_disconnect(), hw_status(), io_check_wiring(), io_read_all(), io_read_input() (+24 more)

### Community 12 - "Community 12"
Cohesion: 0.11
Nodes (25): ApModule, CpxAp, _channel_index_from_port(), compare_topology(), _find_module_by_addr(), generate_topology(), _module_series(), module_to_topology_entry() (+17 more)

### Community 13 - "Community 13"
Cohesion: 0.10
Nodes (3): _incoming_values(), In-memory HAL used to run the exact automation engine without CPX hardware., SimulatedHardware

### Community 14 - "Community 14"
Cohesion: 0.18
Nodes (4): Any, Read a parameter value from a module., Read diagnosis information from a module., Update the status of an existing test run.

### Community 15 - "Community 15"
Cohesion: 0.17
Nodes (12): Dispatch a single test using a pre-connected HardwareInterface.      Each test, _run_single_test_hw(), Execute the test against a single module.      :param hw: Pre-connected Hardwa, run(), Run CC edge validation, then verify successful counters persist., Count rising edges on every resolved route for one CC module., run(), run_with_power_cycle() (+4 more)

### Community 16 - "Community 16"
Cohesion: 0.23
Nodes (10): _get_mounted_valves(), Valve Terminal Condition Counter test.  Uses :class:`hal.HardwareInterface` —, Return ``{module_address: [mounted_valve_slot, ...]}`` from config., Test CC behaviour on VABX valve terminals.      Steps (per valve terminal):, run(), expand_valve_indices(), Valve slot/channel arithmetic.  Product-specific channel counts live in ``BenchC, Return hardware channel indices for one zero-based valve slot. (+2 more)

### Community 17 - "Community 17"
Cohesion: 0.17
Nodes (17): AssignmentScope, ChannelLimits, ConnectionType, PortKind, PowerSupplyConfig, PresenceState, Pydantic models for test bench configuration validation.  Provides typed model, A physical, simulated, or virtual connection between two channels. (+9 more)

### Community 18 - "Community 18"
Cohesion: 0.13
Nodes (8): ConnectionManager, Shared hardware connection manager for the CPX-AP API.  Provides a module-leve, Disconnect without acquiring the lock (caller must hold it)., Thread-safe singleton that manages a single :class:`CpxApHardware` instance., Establish (or replace) the shared hardware connection., Gracefully close the shared connection., Return the shared hardware interface.          Raises:             RuntimeErr, Return the raw module object at *address*.          Convenience for endpoints

### Community 20 - "Community 20"
Cohesion: 0.15
Nodes (7): Abstract interface for persisting test results., Create a new test run record., Record a single test result., Record a measurement., Retrieve recent test runs., Get full detail for a specific run., ResultRepository

### Community 21 - "Community 21"
Cohesion: 0.07
Nodes (15): _auto_reset_output(), _has_dio_direction_parameter(), _is_configurable_output(), _output_channel_indices(), Background callback that resets an output to LOW., Map a UI port to indices in ``mod.channels.outputs``., Return whether the module exposes configurable direction parameter 20145., Return whether an output-process-image channel is configurable DIO. (+7 more)

### Community 22 - "Community 22"
Cohesion: 0.18
Nodes (10): 1 – Install the CPX-IO library, 2 – Install this project's dependencies, 3 – Start the API, 4 – Frontend (development), 4 – Frontend (production), API Stuff, cpx-ap-topology-manager, Repository layout (+2 more)

### Community 23 - "Community 23"
Cohesion: 0.20
Nodes (10): create_basic_test_definitions(), Return a sensible default set of test definitions.      These cover the most c, _cached_test_definitions(), load_all_test_definitions(), Capability-based test resolver.  Matches generic test definitions against modu, Produce an execution plan from a bench configuration.          For each (test_, Return isolated copies of cached test metadata discovered from modules., A concrete test instance bound to a specific module/channel/connection. (+2 more)

### Community 24 - "Community 24"
Cohesion: 0.31
Nodes (8): infer_type_definition_from_instance(), ModuleInstance, ModuleTypeDefinition, Static definition of a module type — capabilities, channel layout, image., A concrete module present (or expected) on a test bench., Construct a BenchConfig directly from live hardware topology., TestBenchMetadata, InstanceCapabilityResolutionTests

### Community 25 - "Community 25"
Cohesion: 0.50
Nodes (5): Yield SSE frames for *run_id*, starting with any buffered log entries., Server-Sent Events stream of log entries for *run_id*.      Connect with ``Eve, _sse_generator(), stream_run_logs(), Request

### Community 26 - "Community 26"
Cohesion: 0.29
Nodes (9): Queue, _child_execute(), _emergency_reset(), execute_resolved_instance(), _instance_from_dict(), Process-isolated execution used exclusively by the FastAPI test service., Child-process target.  It owns both the connection and SafeSession., Reconnect after a killed child and force the bench back to a safe state. (+1 more)

### Community 27 - "Community 27"
Cohesion: 0.25
Nodes (5): get_module_capabilities(), Return conservative category defaults without inspecting product names.      Exp, Return the configured module instance at a bus address., Return the explicit module-type definition for a bus address., Return capabilities for one concrete module.          An explicit instance list

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
Cohesion: 0.33
Nodes (4): ConditionCounterRoute, An output-to-CPX-AP-I-16* input route ready for a CC test., The one-based parameter instance used by ``cpx_io``., Resolve and orient wiring suitable for condition-counter tests.          CC para

### Community 45 - "Community 45"
Cohesion: 0.13
Nodes (21): _diag_locations_text(), _diagnosis_id_from_result(), _expected_channels_from_mounted_valves(), _expected_diag_name(), _format_diag_id(), _get_module_config(), _mounted_valves_from_bench_config(), _mounted_valves_to_text() (+13 more)

## Knowledge Gaps
- **21 isolated node(s):** `cpx-ap-topology-manager`, `Repository layout`, `API Stuff`, `1 – Install the CPX-IO library`, `2 – Install this project's dependencies` (+16 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **5 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `HardwareInterface` connect `Community 4` to `Community 1`, `Community 2`, `Community 3`, `Community 6`, `Community 7`, `Community 9`, `Community 10`, `Community 13`, `Community 14`, `Community 15`, `Community 45`, `Community 16`, `Community 18`, `Community 19`?**
  _High betweenness centrality (0.219) - this node is a cross-community bridge._
- **Why does `BenchConfig` connect `Community 6` to `Community 3`, `Community 4`, `Community 5`, `Community 7`, `Community 8`, `Community 9`, `Community 42`, `Community 45`, `Community 15`, `Community 16`, `Community 17`, `Community 21`, `Community 23`, `Community 24`, `Community 27`, `Community 29`?**
  _High betweenness centrality (0.111) - this node is a cross-community bridge._
- **Why does `CpxApHardware` connect `Community 1` to `Community 2`, `Community 4`, `Community 5`, `Community 6`, `Community 7`, `Community 10`, `Community 15`, `Community 18`, `Community 19`, `Community 26`?**
  _High betweenness centrality (0.102) - this node is a cross-community bridge._
- **Are the 16 inferred relationships involving `HardwareInterface` (e.g. with `AutomationEdge` and `AutomationEngine`) actually correct?**
  _`HardwareInterface` has 16 INFERRED edges - model-reasoned connections that need verification._
- **Are the 18 inferred relationships involving `BenchConfig` (e.g. with `ConfigCompareRequest` and `ConfigGenerateRequest`) actually correct?**
  _`BenchConfig` has 18 INFERRED edges - model-reasoned connections that need verification._
- **Are the 16 inferred relationships involving `CpxApHardware` (e.g. with `ConfigCompareRequest` and `ConfigGenerateRequest`) actually correct?**
  _`CpxApHardware` has 16 INFERRED edges - model-reasoned connections that need verification._
- **Are the 7 inferred relationships involving `FakeHardware` (e.g. with `AutomationEngine` and `AutomationProgram`) actually correct?**
  _`FakeHardware` has 7 INFERRED edges - model-reasoned connections that need verification._