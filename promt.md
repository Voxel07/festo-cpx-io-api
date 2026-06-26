You are a senior software architect and Python/React code reviewer. Your task is to analyze an existing hardware test automation framework and determine whether it complies with the requirements below. Do not rewrite the whole system unless explicitly necessary. Focus on architecture, correctness, maintainability, extensibility, and gaps.

Context:
The framework is intended for dynamic testing of remote IO products, including:

- Input modules
- Output modules
- Modules with both inputs and outputs
- Configurable in/out modules where ports/channels can be configured in software
- Valve terminals

The Python test code should be generic and reusable across multiple test stands. The test stand configuration should be separate from the test code and stored in a separate repository/branch per setup.

The system will use:

- Python for the test runner and hardware interaction
- pytest or a comparable Python test execution approach
- JSONC as configuration format
- PocketBase as the initial database
- Potential future migration from PocketBase to PostgreSQL
- React for the frontend
- React Flow for topology/connection visualization
- GitLab CI for automatic test execution
- No frontend testing is required at the moment

The hardware interaction will use the Python package:
<https://github.com/Festo-se/festo-cpx-io>

Important:

- Do not add frontend tests.
- Do not introduce PostgreSQL now, but evaluate whether the database abstraction would allow a future migration from PocketBase to PostgreSQL.
- Do not hardcode specific module names, product keys, or test bench assumptions into generic test code.
- Keep test logic generic and driven by capabilities, configuration, and wiring information.

Main goals:

1. Check whether the existing framework supports dynamic test assignment.
2. Check whether JSONC configuration is structured well enough for modules, tests, test benches, channels, wiring, and UI visualization.
3. Check whether the Python test code can remain the same across all test stands.
4. Check whether test stand-specific configuration can live in a separate repository and branch.
5. Check whether the result/log storage design in PocketBase is sufficient for later analysis.
6. Check whether the system can be triggered from GitLab CI.
7. Check whether the React/React Flow frontend can visualize the current topology, connected modules, channels, ports, and wiring.
8. Check whether the design allows a future migration from PocketBase to PostgreSQL.

Review the existing codebase and produce a structured assessment.

Requirements to validate:

A. Configuration format and structure

The framework must use JSONC for configuration.

The configuration must support at least the following concepts:

- Test bench metadata:
  - name
  - description
  - version
  - configuration schema version
  - test bench ID
- Module instances:
  - instance ID
  - display name
  - module code
  - product key
  - address
  - type/category
  - module type identifier
  - firmware version if available
  - serial number if available
  - expected/optional/present state
- Module type definitions:
  - module code
  - product family
  - number of inputs
  - number of outputs
  - number of configurable channels
  - valve count where applicable
  - supported capabilities
  - channel definitions
  - electrical/safety limits where applicable
  - image asset path
- Channel definitions:
  - channel index
  - port index if different from channel index
  - channel name
  - supported modes
  - default mode
  - current mode
  - capabilities
  - limits
  - UI anchor position for visualization
  - UI hotspot/port geometry for visualization
- Wiring/connections:
  - source module instance ID
  - source channel/port
  - target module instance ID
  - target channel/port
  - signal type
  - direction
  - expected behavior
  - whether the connection is physical, simulated, or virtual
- Test definitions:
  - stable test ID
  - test name
  - description
  - version
  - required capabilities
  - required wiring type
  - supported module categories
  - safety classification
  - whether the test is allowed in CI
  - whether the test can run in parallel
  - parameters/default parameters
- Test compatibility:
  - based on capabilities rather than only module names
  - optional include/exclude rules by module code
  - optional include/exclude rules by product key
  - support for negative test modules, for example modules that are intentionally expected to fail
- UI visualization metadata:
  - image path
  - module position on canvas
  - channel anchor positions
  - channel hotspot definitions
  - connection rendering metadata if present

Check whether the current JSONC schema/configuration supports these concepts. If not, propose concrete additions.

B. Configuration validation

Check whether the framework validates JSONC configuration before test execution.

Validation should catch:

- invalid JSONC syntax
- missing required fields
- duplicate module instance IDs
- duplicate product keys if not explicitly allowed
- duplicate addresses on the same bus/topology where invalid
- connections to nonexistent modules
- connections to nonexistent channels
- invalid channel modes
- tests referencing nonexistent capabilities
- tests referencing nonexistent modules
- circular or ambiguous wiring where relevant
- unsafe test/wiring combinations
- incompatible test assignments
- missing image assets for UI visualization
- invalid UI coordinates/hotspots
- schema version mismatch

Prefer a typed validation approach using Pydantic, JSON Schema, or equivalent.

Check whether validation failures are clear and actionable.

C. Dynamic test assignment

Check whether test assignment is capability-driven.

The expected model is:

- Generic test declares requirements.
- Module/channel/wiring configuration declares capabilities.
- The resolver creates concrete test instances from compatible combinations.
- The resolved execution plan is stored or exportable before execution.

The framework should not rely only on explicit lists like `CompatibleTestcases` inside every module instance. Such lists may exist as overrides, but the primary assignment mechanism should be capability-based.

Check whether the resolver supports:

- module-level tests
- channel-level tests
- wiring/loopback tests
- configurable channel mode tests
- valve terminal tests
- include/exclude by module code
- include/exclude by product key
- expected-failure modules or intentionally failing test objects
- dry-run mode showing the planned tests without touching hardware
- filtering by test ID, module, module code, product key, capability, or safety class

D. Python test architecture

Check whether the Python test code is generic and reusable across different test stands.

Verify:

- hardware-specific access is isolated in a hardware abstraction layer
- interaction with `festo-cpx-io` is wrapped behind interfaces
- tests do not directly parse raw config everywhere
- tests receive resolved module/channel/wiring objects from fixtures or dependency injection
- tests do not hardcode test bench names, addresses, product keys, or module names
- safe setup and teardown exist
- outputs are returned to safe state after every test
- exceptions still trigger safe teardown
- test runner supports local execution and GitLab CI execution
- test runner can run without UI
- test runner has a dry-run/planning mode
- test runner can export the resolved execution plan
- test runner stores the test code commit and configuration commit with results

If pytest is used, check for:

- fixtures for bench/session/module/channel setup
- dynamic parametrization or a custom pytest plugin
- JUnit XML output for GitLab CI
- clear test IDs in reports
- markers for safety class, hardware-required tests, long-running tests, etc.

E. GitLab CI integration

Check whether GitLab CI can:

- clone or receive the test code repository
- clone or receive the configuration repository
- select the correct configuration branch
- run configuration validation
- run dry-run planning
- run hardware tests on tagged hardware runners
- prevent concurrent jobs from using the same test bench
- export JUnit XML results
- upload logs and artifacts
- store result metadata in PocketBase
- record GitLab pipeline ID and job ID
- record test code commit SHA
- record configuration commit SHA

Check whether the framework supports environment variables or CLI parameters such as:

- CONFIG_REPO_PATH
- CONFIG_REF
- TESTBENCH_ID
- POCKETBASE_URL
- POCKETBASE_TOKEN
- GITLAB_PIPELINE_ID
- GITLAB_JOB_ID
- DRY_RUN
- TEST_FILTER
- SAFETY_CLASS_FILTER

F. Database/logging design with PocketBase

PocketBase is the initial database. Check whether the data model is suitable for structured analysis and possible migration to PostgreSQL later.

At minimum, the system should store:

- test runs
- test case results
- measurements
- structured log events
- artifacts
- resolved execution plans
- module snapshots
- wiring snapshots
- configuration metadata
- software versions
- hardware/firmware versions if available

Check whether records include:

- run ID
- test case ID
- resolved test instance ID
- test name
- test version
- module instance ID
- module code
- product key
- channel/port
- verdict
- start timestamp
- end timestamp
- duration
- failure reason
- exception type
- stack trace where applicable
- measured values
- units
- lower/upper limits
- raw log reference
- artifact reference

Assess:

- whether logs are structured, not only plain text
- whether large files are stored as file artifacts rather than huge database fields
- whether database access is abstracted behind a repository/service layer
- whether business logic is not tightly coupled to PocketBase APIs
- whether identifiers and schemas would be reasonably portable to PostgreSQL
- whether timestamps use a consistent timezone, preferably UTC
- whether migrations/schema evolution are considered
- whether failed or interrupted runs are handled correctly

G. Frontend and visualization

The frontend uses React and React Flow. No frontend testing is required.

Check whether the frontend can:

- load current test bench configuration
- render modules as nodes
- render wiring/connections as edges
- show module images from SVG/PNG assets
- show channel/port positions or hotspots
- show module metadata
- show channel metadata
- show current test run status
- show historical test statistics
- show pass/fail/running status per module/channel/test
- show logs for a selected run/test
- distinguish unconnected, connected, failed, passed, disabled, and running states
- handle configurable in/out channels and display their current mode
- handle valve terminals
- support large enough topologies without becoming unusable

Check whether React Flow node/edge IDs are stable and derived from module instance IDs and channel IDs, not display names.

Check whether frontend state is separated from backend persistence.

Do not add or require frontend tests.

H. Safety and hardware protection

Check whether the framework includes safety mechanisms:

- safe startup state
- safe teardown state
- output reset after tests
- test bench lock/reservation
- prevention of parallel unsafe tests
- safety classes for tests
- current/voltage/pressure limits in configuration
- explicit opt-in for destructive, stress, or negative tests
- validation of wiring before output activation
- timeout handling
- communication loss handling
- emergency stop or external interlock integration if available
- audit log for output-changing actions

I. Expected output from your review

Produce the final review in this structure:

1. Executive summary
   - Overall compliance level: High / Medium / Low
   - Main strengths
   - Main risks
   - Most urgent recommendations

2. Requirement compliance matrix
   Create a table with columns:
   - Area
   - Status: Compliant / Partially compliant / Missing / Not applicable
   - Evidence in code/config
   - Gaps
   - Recommended action

3. Architecture assessment
   - Current architecture summary
   - Whether responsibilities are clearly separated
   - Coupling concerns
   - Scalability concerns
   - Maintainability concerns

4. Configuration assessment
   - JSONC structure review
   - Schema/validation review
   - Recommended config changes
   - Example improved JSONC snippets if useful

5. Dynamic test assignment assessment
   - How assignment currently works
   - Whether it is capability-driven
   - Missing resolver features
   - Recommended resolver changes

6. Python test runner assessment
   - Test framework design
   - pytest/fixture/plugin usage if applicable
   - hardware abstraction
   - safe setup/teardown
   - CI suitability

7. PocketBase data model assessment
   - Existing collections/tables
   - Missing collections/fields
   - Logging/result traceability
   - Migration readiness for PostgreSQL
   - Recommended schema changes

8. GitLab CI assessment
   - Current CI flow
   - Missing CI variables
   - Runner/bench locking concerns
   - Artifact/result handling
   - Recommended `.gitlab-ci.yml` changes if needed

9. React/React Flow frontend assessment
   - Topology visualization
   - Module image handling
   - Channel/port representation
   - Status/statistics/log views
   - Gaps and recommendations
   - Do not recommend frontend tests

10. Safety assessment
    - Existing safety mechanisms
    - Missing safety mechanisms
    - High-risk areas
    - Recommended safety improvements

11. Prioritized action plan
    Split into:
    - Must fix before hardware execution
    - Should fix before scaling to multiple test benches
    - Nice to have

12. Concrete code/config recommendations
    - Include file paths where changes are recommended
    - Include example JSONC schema/config changes
    - Include example Python interface changes
    - Include example database collection changes
    - Include example CI changes
    - Keep examples minimal and focused

Review rules:

- Be specific. Reference actual files, classes, functions, configs, and collections where possible.
- Do not make vague recommendations without explaining what to change.
- Do not propose a complete rewrite unless there is no reasonable incremental path.
- Prefer incremental refactoring.
- Do not add frontend tests.
- Do not replace PocketBase now.
- Do not hardcode module-specific logic into generic tests.
- Keep future PostgreSQL migration in mind, but do not implement it now.
- If information is missing, state exactly what information is missing and what assumption you used.



