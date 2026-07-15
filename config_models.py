"""Pydantic models for test bench configuration validation.

Provides typed models for all configuration concepts: test bench metadata,
module instances, module type definitions, channel definitions, wiring,
test definitions, and UI visualization metadata.

Usage::

    from config_io import load_bench_config
    config = load_bench_config("data/bench_config.json")
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

# ─── Enums ────────────────────────────────────────────────────────────────────


class ModuleCategory(str, Enum):
    INPUT = "input"
    OUTPUT = "output"
    INOUT = "inout"
    BUS = "bus"
    VALVE = "valve"
    INTERFACE = "interface"



class SafetyClass(str, Enum):
    SAFE = "safe"
    CAUTION = "caution"
    DESTRUCTIVE = "destructive"


class SignalType(str, Enum):
    DIGITAL_24V = "digital_24v"
    ANALOG_0_10V = "analog_0_10v"
    ANALOG_4_20MA = "analog_4_20ma"
    IO_LINK = "io_link"


class ConnectionType(str, Enum):
    PHYSICAL = "physical"
    SIMULATED = "simulated"
    VIRTUAL = "virtual"


class PortKind(str, Enum):
    IN = "in"
    OUT = "out"
    INOUT = "inout"


class PresenceState(str, Enum):
    EXPECTED = "expected"
    OPTIONAL = "optional"
    PRESENT = "present"
    MISSING = "missing"


class AssignmentScope(str, Enum):
    SYSTEM = "system"
    MODULE = "module"
    CHANNEL = "channel"
    WIRING = "wiring"


# ─── Channel / Port ───────────────────────────────────────────────────────────


class ChannelLimits(BaseModel):
    """Electrical/safety limits for a channel."""

    max_voltage_v: float | None = Field(None, ge=0, description="Maximum voltage in Volts")
    max_current_ma: float | None = Field(None, ge=0, description="Maximum current in milliamperes")
    max_pressure_bar: float | None = Field(None, ge=0, description="Maximum pressure in bar")


class ChannelDefinition(BaseModel):
    """Definition of a single channel/port on a module type."""

    index: int = Field(..., ge=0, description="0-based channel index")
    port_index: int | None = Field(None, description="Port index if different from channel index")
    name: str = Field("", description="Human-readable channel name (e.g. 'X0', 'DI 0')")
    supported_modes: list[str] = Field(default_factory=list, description="Supported operating modes")
    default_mode: str = Field("", description="Default operating mode")
    current_mode: str = Field("", description="Currently configured mode")
    capabilities: list[str] = Field(default_factory=list, description="Channel-specific capabilities")
    ui_anchor_x: float | None = Field(None, ge=0, le=1, description="UI anchor X (0-1 fraction of image)")
    ui_anchor_y: float | None = Field(None, ge=0, le=1, description="UI anchor Y (0-1 fraction of image)")
    ui_hotspot_radius: float | None = Field(None, ge=0, description="UI hotspot radius in pixels")
    limits: ChannelLimits | None = Field(None, description="Electrical/safety limits for the channel")

    @model_validator(mode="after")
    def _validate_modes(self) -> ChannelDefinition:
        supported = set(self.supported_modes)
        for field_name, mode in (("default_mode", self.default_mode), ("current_mode", self.current_mode)):
            if mode and supported and mode not in supported:
                raise ValueError(f"{field_name} {mode!r} is not present in supported_modes")
        if self.ui_hotspot_radius is not None and self.ui_hotspot_radius == 0:
            raise ValueError("ui_hotspot_radius must be greater than zero")
        return self


# ─── Module Type Definition ───────────────────────────────────────────────────


class ModuleTypeDefinition(BaseModel):
    """Static definition of a module type — capabilities, channel layout, image."""

    module_code: int = Field(..., description="Numeric module code")
    product_family: str = Field("", description="Stable product family identifier")
    capabilities: list[str] = Field(
        default_factory=list,
        description="Supported capabilities: digital_input, digital_output, condition_counter, etc.",
    )
    num_inputs: int = Field(0, ge=0)
    num_outputs: int = Field(0, ge=0)
    num_configurable: int = Field(0, ge=0, description="Configurable in/out channels")
    valve_count: int = Field(0, ge=0, description="Number of valve slots (VABX only)")
    channels_per_valve: int = Field(0, ge=0, description="Hardware output channels per valve slot")
    channels: list[ChannelDefinition] = Field(default_factory=list)
    image_asset: str = Field("", description="SVG/PNG filename for UI")
    test_parameters: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Product-specific parameters keyed by test ID",
    )


# ─── Test Bench Metadata ──────────────────────────────────────────────────────


class TestBenchMetadata(BaseModel):
    id: str = Field(..., description="Unique test bench identifier")
    name: str = Field("", description="Display name")
    description: str = Field("", description="Human-readable description")
    ip_address: str = Field("", description="Default IP address of the CPX-AP gateway")
    version: str = Field("1.0", description="Configuration version (human-managed)")


# ─── Module Instance ──────────────────────────────────────────────────────────


class ModuleInstance(BaseModel):
    """A concrete module present (or expected) on a test bench."""

    instance_id: str = Field(..., description="Unique instance ID, e.g. 'mod-003'")
    display_name: str = Field("", description="Human-readable label")
    module_code: int = Field(..., description="Numeric module code")
    product_key: str | None = Field(None, description="Unique product key (serial-like)")
    address: int = Field(..., ge=0, description="Bus address (0-based position)")
    category: ModuleCategory = Field(..., description="Input / Output / InOut / Bus / Valve")
    module_type_ref: str = Field(
        "",
        description="Key into module_types map for this module's type definition",
    )
    capabilities: list[str] | None = Field(
        default=None,
        description=(
            "Authoritative capabilities of this concrete module. When omitted, "
            "module_type capabilities are used only as a legacy fallback."
        ),
    )
    firmware_version: str | None = Field(None)
    serial_number: str | None = Field(None)
    presence_state: PresenceState = Field(PresenceState.EXPECTED)
    compatible_tests_override: list[str] = Field(
        default_factory=list,
        description="Explicit test IDs to include (overrides capability matching)",
    )
    excluded_tests: list[str] = Field(
        default_factory=list,
        description="Test IDs to exclude even if capabilities match",
    )
    is_negative_test_target: bool = Field(
        False,
        description="If True, this module is expected to fail certain tests",
    )
    mounted_valves: list[int] = Field(
        default_factory=list,
        description="Indices of currently mounted valves (for VABX bodies)"
    )
    valve_slots: int | None = Field(
        default=None,
        description="Total number of physical valve slots on this block (for VMPAL modular bodies)"
    )
    num_inputs: int = Field(0, ge=0, description="Number of digital/analog input channels")
    num_outputs: int = Field(0, ge=0, description="Number of digital/analog output channels")
    num_inouts: int = Field(0, ge=0, description="Number of bidirectional/configurable channels")
    port_directions: dict[str, bool] = Field(
        default_factory=dict,
        description="Direction of configurable ports. True = output, False = input. Key is channel string index.",
    )


# ─── Wiring / Connection ──────────────────────────────────────────────────────


class WiringConnection(BaseModel):
    """A physical, simulated, or virtual connection between two channels."""

    id: str = Field(..., description="Stable connection ID")
    source_instance_id: str = Field(..., description="Source module instance_id")
    source_channel: str = Field(..., description="Source port label, e.g. 'X0'")
    target_instance_id: str = Field(..., description="Target module instance_id")
    target_channel: str = Field(..., description="Target port label, e.g. 'X0'")
    signal_type: SignalType = Field(SignalType.DIGITAL_24V)
    direction: str = Field("output_to_input", description="Signal direction")
    connection_type: ConnectionType = Field(ConnectionType.PHYSICAL)
    expected_behavior: str = Field("", description="e.g. 'pulse_propagation'")
    waypoints: list[dict[str, float]] = Field(
        default_factory=list, description="UI routing waypoints [{x, y}, ...]"
    )
    # UI rendering hints – preserved so edges reconstruct correctly after load
    source_handle: str | None = Field(None, description="ReactFlow source handle ID, e.g. 'src-inout-X0'")
    target_handle: str | None = Field(None, description="ReactFlow target handle ID, e.g. 'tgt-inout-X1'")
    straight: bool = Field(False, description="Use straight-line wire routing instead of stepped routing")
    label_offset: dict[str, float] | None = Field(None, description="UI label offset {x, y}")


# ─── Test Definition ──────────────────────────────────────────────────────────


class TestDefinition(BaseModel):
    """A generic test that can be matched against module capabilities."""

    test_id: str = Field(..., description="Stable test identifier")
    name: str = Field(..., description="Human-readable test name")
    version: str = Field("1.0.0", description="Semantic version")
    description: str = Field("")
    required_capabilities: list[str] = Field(
        default_factory=list,
        description="Capabilities a module must have for this test to apply",
    )
    required_wiring_type: ConnectionType | None = Field(
        None, description="Required wiring type (None = any)"
    )
    supported_categories: list[ModuleCategory] = Field(
        default_factory=list,
        description="Module categories this test supports",
    )
    safety_class: SafetyClass = Field(SafetyClass.SAFE)
    allowed_in_ci: bool = Field(True, description="Whether this test can run in CI pipelines")
    can_run_parallel: bool = Field(False, description="Whether test instances can run in parallel")
    assignment_scope: AssignmentScope = Field(
        AssignmentScope.MODULE,
        description="Whether this definition expands per system, module, channel, or wiring",
    )
    required_channel_capabilities: list[str] = Field(default_factory=list)
    required_channel_modes: list[str] = Field(default_factory=list)
    target_module_instance_ids: list[str] = Field(default_factory=list)
    singleton: bool = Field(
        False,
        description="If True, only one instance is created regardless of how many modules match (e.g. system-wide tests)",
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Default test parameters (pulse_duration_s, toggle_cycles, etc.)",
    )
    include_module_codes: list[int] = Field(
        default_factory=list,
        description="Restrict to specific module codes (empty = no restriction)",
    )
    exclude_module_codes: list[int] = Field(
        default_factory=list,
        description="Exclude specific module codes",
    )
    include_product_keys: list[str] = Field(
        default_factory=list,
        description="Restrict to specific product keys (empty = no restriction)",
    )
    exclude_product_keys: list[str] = Field(
        default_factory=list,
        description="Exclude specific product keys",
    )
# ─── UI Visualization Metadata ────────────────────────────────────────────────


class UIModulePosition(BaseModel):
    instance_id: str
    x: float = 0
    y: float = 0
    image_path: str = ""


class UIChannelAnchor(BaseModel):
    instance_id: str
    channel_index: int
    anchor_x: float = Field(..., ge=0, le=1)
    anchor_y: float = Field(..., ge=0, le=1)
    hotspot_radius: float = Field(10.0, gt=0)


class UIVisualizationMetadata(BaseModel):
    module_positions: list[UIModulePosition] = Field(default_factory=list)
    channel_anchors: list[UIChannelAnchor] = Field(default_factory=list)


def get_module_capabilities(display_name: str, category: str) -> list[str]:
    """Return conservative category defaults without inspecting product names.

    Explicit ``module_types`` remain authoritative.  These defaults only keep
    generated legacy configurations usable until their capabilities are stored.
    """
    del display_name
    defaults = {
        "input": ["digital_input", "remanent_params"],
        "output": ["digital_output", "remanent_params"],
        "inout": [
            "digital_input", "digital_output", "configurable_io",
            "remanent_params",
        ],
        "valve": ["digital_output", "valve_output", "remanent_params"],
        "bus": ["system_diagnosis"],
        "interface": ["system_diagnosis"],
    }
    return list(defaults.get(category, ()))


def infer_type_definition_from_instance(mod: ModuleInstance) -> ModuleTypeDefinition:
    code = mod.module_code
    category = mod.category
    num_in = mod.num_inputs
    num_out = mod.num_outputs
    num_io = mod.num_inouts
    valve_count = mod.valve_slots or len(mod.mounted_valves)
    channels_per_valve = (
        max(1, num_out // valve_count)
        if category == ModuleCategory.VALVE and num_out and valve_count
        else (2 if category == ModuleCategory.VALVE else 0)
    )
        
    caps = get_module_capabilities(mod.display_name, category.value)
    
    channels = []
    max_ch = max(num_in, num_out, num_io, valve_count)
    for ch_idx in range(max_ch):
        ch_caps = []
        supported_modes: list[str] = []
        default_mode = ""
        current_mode = ""
        if num_out > 0 or category == ModuleCategory.VALVE:
            ch_caps.append("digital_output")
        if num_in > 0:
            ch_caps.append("digital_input")
        if num_io > 0:
            ch_caps.append("configurable_io")
            supported_modes = ["input", "output"]
            default_mode = "input"
            current_mode = "output" if mod.port_directions.get(str(ch_idx)) else "input"
            
        channels.append(
            ChannelDefinition(
                index=ch_idx,
                name=f"X{ch_idx}",
                capabilities=ch_caps,
                supported_modes=supported_modes,
                default_mode=default_mode,
                current_mode=current_mode,
            )
        )
        
    return ModuleTypeDefinition(
        module_code=code,
        product_family=category.value,
        capabilities=caps,
        num_inputs=num_in,
        num_outputs=num_out,
        num_configurable=num_io,
        valve_count=valve_count,
        channels_per_valve=channels_per_valve,
        channels=channels,
    )


class PowerSupplyConfig(BaseModel):
    """Power supply configuration parameters."""
    model_config = ConfigDict(populate_by_name=True)

    comport: str | None = Field(None, alias="ComPort")
    ip_address: str | None = Field(None, alias="Ip addr")
    pl_channel: int | None = Field(None, alias="pl_channel")
    ps_channel: int | None = Field(None, alias="ps_channel")


# ─── Top-level Bench Configuration ────────────────────────────────────────────


class BenchConfig(BaseModel):
    """Complete test bench configuration."""

    schema_version: str = Field("1.0", description="Configuration schema version")
    test_bench: TestBenchMetadata
    power_supply: PowerSupplyConfig | None = Field(None, alias="power_supply")
    module_types: dict[str, ModuleTypeDefinition] = Field(
        default_factory=dict,
        description="Module type definitions keyed by type reference string",
    )
    module_instances: list[ModuleInstance] = Field(..., min_length=1)
    wiring: list[WiringConnection] = Field(default_factory=list)
    test_definitions: list[TestDefinition] = Field(default_factory=list)
    ui_metadata: UIVisualizationMetadata = Field(default_factory=UIVisualizationMetadata)

    def module_instance_at(self, address: int) -> ModuleInstance:
        """Return the configured module instance at a bus address."""
        for module in self.module_instances:
            if module.address == address:
                return module
        raise KeyError(f"No configured module at address {address}")

    def module_type_at(self, address: int) -> ModuleTypeDefinition:
        """Return the explicit module-type definition for a bus address."""
        module = self.module_instance_at(address)
        return self.module_types[module.module_type_ref]

    def module_capabilities(self, module: ModuleInstance | int) -> set[str]:
        """Return capabilities for one concrete module.

        An explicit instance list is authoritative, including an intentionally
        empty list. Type/category inheritance exists only for older configs.
        """
        instance = self.module_instance_at(module) if isinstance(module, int) else module
        if instance.capabilities is not None:
            return set(instance.capabilities)
        module_type = self.module_types.get(instance.module_type_ref)
        if module_type is not None:
            return set(module_type.capabilities)
        return set(get_module_capabilities(instance.display_name, instance.category.value))

    @model_validator(mode="after")
    def _populate_module_types(self) -> BenchConfig:
        if not self.module_types:
            types = {}
            for mod in self.module_instances:
                type_ref = mod.module_type_ref or f"type-{mod.module_code}"
                if type_ref not in types:
                    type_def = infer_type_definition_from_instance(mod)
                    # Prefer explicit instance-level IO counts over name-inferred values
                    if mod.num_inputs or mod.num_outputs or mod.num_inouts:
                        type_def.num_inputs = mod.num_inputs
                        type_def.num_outputs = mod.num_outputs
                        type_def.num_configurable = mod.num_inouts
                    types[type_ref] = type_def
            self.module_types = types
        return self

    @model_validator(mode="after")
    def _check_duplicate_instance_ids(self) -> BenchConfig:
        ids = [m.instance_id for m in self.module_instances]
        seen: set[str] = set()
        dupes = {i for i in ids if i in seen or seen.add(i)}  # type: ignore[func-returns-value]
        if dupes:
            raise ValueError(f"Duplicate module instance IDs: {sorted(dupes)}")
        return self

    @model_validator(mode="after")
    def _check_duplicate_addresses(self) -> BenchConfig:
        addrs = [m.address for m in self.module_instances]
        seen: set[int] = set()
        dupes = {a for a in addrs if a in seen or seen.add(a)}  # type: ignore[func-returns-value]
        if dupes:
            raise ValueError(f"Duplicate module addresses on same bus: {sorted(dupes)}")
        return self

    @model_validator(mode="after")
    def _check_wiring_references(self) -> BenchConfig:
        mod_ids = {m.instance_id for m in self.module_instances}
        for w in self.wiring:
            if w.source_instance_id not in mod_ids:
                raise ValueError(
                    f"Wiring '{w.id}': source instance '{w.source_instance_id}' not found"
                )
            if w.target_instance_id not in mod_ids:
                raise ValueError(
                    f"Wiring '{w.id}': target instance '{w.target_instance_id}' not found"
                )
        return self

    @model_validator(mode="after")
    def _check_test_references(self) -> BenchConfig:
        """Reject impossible capability and explicit module references."""
        available_capabilities: set[str] = set()
        for module in self.module_instances:
            available_capabilities.update(self.module_capabilities(module))
        for module_type in self.module_types.values():
            for channel in module_type.channels:
                available_capabilities.update(channel.capabilities)
        module_ids = {module.instance_id for module in self.module_instances}
        for test in self.test_definitions:
            requested = set(test.required_capabilities) | set(test.required_channel_capabilities)
            missing = requested - available_capabilities
            if missing:
                raise ValueError(
                    f"Test '{test.test_id}' references unavailable capabilities: {sorted(missing)}"
                )
            unknown_modules = set(test.target_module_instance_ids) - module_ids
            if unknown_modules:
                raise ValueError(
                    f"Test '{test.test_id}' references unknown module instances: "
                    f"{sorted(unknown_modules)}"
                )
        return self

    @model_validator(mode="after")
    def _check_module_type_references(self) -> BenchConfig:
        for module in self.module_instances:
            if not module.module_type_ref:
                module.module_type_ref = f"type-{module.module_code}"
            if module.module_type_ref not in self.module_types:
                raise ValueError(
                    f"Module '{module.instance_id}' references unknown module type "
                    f"'{module.module_type_ref}'"
                )
        return self

    @model_validator(mode="after")
    def _check_duplicate_wiring_ids_and_endpoints(self) -> BenchConfig:
        ids: set[str] = set()
        endpoints: set[tuple[str, str, str, str]] = set()
        for wire in self.wiring:
            if wire.id in ids:
                raise ValueError(f"Duplicate wiring ID: {wire.id!r}")
            ids.add(wire.id)
            endpoint = (
                wire.source_instance_id,
                wire.source_channel,
                wire.target_instance_id,
                wire.target_channel,
            )
            reverse = (endpoint[2], endpoint[3], endpoint[0], endpoint[1])
            if endpoint[0:2] == endpoint[2:4]:
                raise ValueError(f"Wiring '{wire.id}' connects a channel to itself")
            if endpoint in endpoints or reverse in endpoints:
                raise ValueError(f"Ambiguous duplicate wiring endpoints for '{wire.id}'")
            endpoints.add(endpoint)
            if wire.direction != "output_to_input":
                raise ValueError(
                    f"Wiring '{wire.id}' has unsupported direction {wire.direction!r}; "
                    "expected 'output_to_input'"
                )
        return self

    @model_validator(mode="after")
    def _check_schema_version(self) -> BenchConfig:
        supported = {"1.0"}
        if self.schema_version not in supported:
            raise ValueError(
                f"Unsupported schema version '{self.schema_version}'. "
                f"Supported: {sorted(supported)}"
            )
        return self

    @model_validator(mode="after")
    def _check_duplicate_product_keys(self) -> BenchConfig:
        """Product keys identify physical products and must be unique per bench."""
        keys = [m.product_key for m in self.module_instances if m.product_key]
        seen: set[str] = set()
        dupes = {k for k in keys if k in seen or seen.add(k)}
        if dupes:
            raise ValueError(f"Duplicate product keys found: {sorted(dupes)}")
        return self

    @model_validator(mode="after")
    def _check_wiring_channels(self) -> BenchConfig:
        """Verify wiring references valid source and target channels."""
        for w in self.wiring:
            # Source checks
            src_mod = next((m for m in self.module_instances if m.instance_id == w.source_instance_id), None)
            if src_mod:
                type_def = self.module_types.get(src_mod.module_type_ref)
                if type_def and type_def.channels:
                    ch_names = {c.name for c in type_def.channels}
                    ch_indices = {str(c.index) for c in type_def.channels}
                    fallback_names = {f"out{c.index}" for c in type_def.channels} | \
                                     {f"in{c.index}" for c in type_def.channels} | \
                                     {f"vabxin{c.index}" for c in type_def.channels}
                    valid_names = ch_names | ch_indices | fallback_names
                    if w.source_channel not in valid_names:
                        raise ValueError(
                            f"Wiring '{w.id}': source channel '{w.source_channel}' "
                            f"not found on module '{src_mod.instance_id}'. Valid: {valid_names}"
                        )
            # Target checks
            tgt_mod = next((m for m in self.module_instances if m.instance_id == w.target_instance_id), None)
            if tgt_mod:
                type_def = self.module_types.get(tgt_mod.module_type_ref)
                if type_def and type_def.channels:
                    ch_names = {c.name for c in type_def.channels}
                    ch_indices = {str(c.index) for c in type_def.channels}
                    # Also accept UI's fallback handle formats: 'out0', 'in0', 'vabxin0'
                    fallback_names = {f"out{c.index}" for c in type_def.channels} | \
                                     {f"in{c.index}" for c in type_def.channels} | \
                                     {f"vabxin{c.index}" for c in type_def.channels}
                    valid_names = ch_names | ch_indices | fallback_names
                    if w.target_channel not in valid_names:
                        raise ValueError(
                            f"Wiring '{w.id}': target channel '{w.target_channel}' "
                            f"not found on module '{tgt_mod.instance_id}'. Valid: {valid_names}"
                        )
        return self

    @model_validator(mode="after")
    def _check_ui_references(self) -> BenchConfig:
        module_ids = {module.instance_id for module in self.module_instances}
        positioned: set[str] = set()
        for position in self.ui_metadata.module_positions:
            if position.instance_id not in module_ids:
                raise ValueError(
                    f"UI position references unknown module '{position.instance_id}'"
                )
            if position.instance_id in positioned:
                raise ValueError(f"Duplicate UI position for module '{position.instance_id}'")
            positioned.add(position.instance_id)
        anchors: set[tuple[str, int]] = set()
        for anchor in self.ui_metadata.channel_anchors:
            if anchor.instance_id not in module_ids:
                raise ValueError(
                    f"UI channel anchor references unknown module '{anchor.instance_id}'"
                )
            key = (anchor.instance_id, anchor.channel_index)
            if key in anchors:
                raise ValueError(
                    f"Duplicate UI channel anchor for '{anchor.instance_id}' "
                    f"channel {anchor.channel_index}"
                )
            anchors.add(key)
            module = next(item for item in self.module_instances if item.instance_id == anchor.instance_id)
            module_type = self.module_types[module.module_type_ref]
            valid_channels = {channel.index for channel in module_type.channels}
            if valid_channels and anchor.channel_index not in valid_channels:
                raise ValueError(
                    f"UI channel anchor references unknown channel {anchor.channel_index} "
                    f"on module '{anchor.instance_id}'"
                )
        return self

    @classmethod
    def from_hardware(
        cls,
        live_modules: list[Any],
        ip_address: str,
        bench_id: str = "default",
    ) -> BenchConfig:
        """Construct a BenchConfig directly from live hardware topology."""
        # Build test bench metadata
        meta = TestBenchMetadata(
            id=bench_id,
            name=f"Bench {bench_id}",
            description="Auto-generated configuration from live hardware",
            ip_address=ip_address,
            version="1.0",
        )

        def infer_cat(is_valve: bool, num_in: int, num_out: int, num_io: int) -> ModuleCategory:
            if is_valve:
                return ModuleCategory.VALVE
            if num_io > 0 or (num_in > 0 and num_out > 0):
                return ModuleCategory.INOUT
            if num_out > 0:
                return ModuleCategory.OUTPUT
            if num_in > 0:
                return ModuleCategory.INPUT
            return ModuleCategory.BUS

        def infer_caps(is_valve: bool, num_in: int, num_out: int, num_io: int) -> list[str]:
            caps = []
            if num_in > 0:
                caps.append("digital_input")
            if num_out > 0:
                caps.append("digital_output")
            if num_io > 0:
                caps.append("configurable_io")
            if is_valve:
                caps.append("valve_output")
            if not (num_in or num_out or num_io or is_valve):
                caps.append("system_diagnosis")
            return list(set(caps))

        instances: list[ModuleInstance] = []
        type_defs: dict[str, ModuleTypeDefinition] = {}

        for m in live_modules:
            addr = m.address
            m_code = m.module_code
            p_key = m.product_key
            name = m.name

            inst_id = f"mod-{addr:03d}"
            type_ref = f"type-{m_code}"

            cat = infer_cat(m.is_valve, m.num_inputs, m.num_outputs, m.num_inouts)
            caps = infer_caps(m.is_valve, m.num_inputs, m.num_outputs, m.num_inouts)

            # Build channel definitions dynamically
            channels = []
            for ch_idx in range(max(m.num_inputs, m.num_outputs, m.num_inouts)):
                ch_caps = []
                if m.num_outputs > 0 or cat == ModuleCategory.VALVE:
                    ch_caps.append("digital_output")
                if m.num_inputs > 0:
                    ch_caps.append("digital_input")
                if m.num_inouts > 0:
                    ch_caps.append("configurable_io")

                channels.append(
                    ChannelDefinition(
                        index=ch_idx,
                        name=f"X{ch_idx}",
                        capabilities=ch_caps,
                    )
                )

            if type_ref not in type_defs:
                type_defs[type_ref] = ModuleTypeDefinition(
                    module_code=m_code,
                    product_family=str(m_code),
                    capabilities=caps,
                    num_inputs=m.num_inputs,
                    num_outputs=m.num_outputs,
                    num_configurable=m.num_inouts,
                    valve_count=(m.num_outputs // 2) if cat == ModuleCategory.VALVE else 0,
                    channels_per_valve=2 if cat == ModuleCategory.VALVE else 0,
                    channels=channels,
                )

            instances.append(
                ModuleInstance(
                    instance_id=inst_id,
                    display_name=name,
                    module_code=m_code,
                    product_key=p_key,
                    address=addr,
                    category=cat,
                    module_type_ref=type_ref,
                    capabilities=caps,
                    mounted_valves=list(range(16)) if cat == ModuleCategory.VALVE else [],
                    num_inputs=m.num_inputs,
                    num_outputs=m.num_outputs,
                    num_inouts=m.num_inouts,
                )
            )

        return cls(
            schema_version="1.0",
            test_bench=meta,
            module_types=type_defs,
            module_instances=instances,
            wiring=[],
            test_definitions=[],
        )


def create_basic_test_definitions() -> list[TestDefinition]:
    """Return a sensible default set of test definitions.

    These cover the most common CPX-AP module types and can be overridden
    in bench-specific config files.
    """
    return [
        TestDefinition(
            test_id="connection-validation",
            name="Connection Validation",
            version="1.0.0",
            description="Pulse source outputs and verify target inputs to validate wiring",
            required_capabilities=["digital_output"],
            required_wiring_type=ConnectionType.PHYSICAL,
            supported_categories=[ModuleCategory("output"), ModuleCategory("input"), ModuleCategory("inout")],
            safety_class=SafetyClass.SAFE,
            allowed_in_ci=True,
            can_run_parallel=False,
            parameters={"pulse_duration_s": 0.3},
        ),
        TestDefinition(
            test_id="condition-counter",
            name="Condition Counter",
            version="1.0.0",
            description="Read and verify condition counter parameters",
            required_capabilities=["condition_counter"],
            supported_categories=[ModuleCategory("output"), ModuleCategory("input"), ModuleCategory("inout")],
            safety_class=SafetyClass.SAFE,
            allowed_in_ci=True,
            parameters={"cc_param_id": 20094, "cc_readback_param_id": 20095},
        ),
        TestDefinition(
            test_id="valve-condition-counter",
            name="Valve Condition Counter",
            version="1.0.0",
            description="Set CC setpoint, toggle valves past threshold, verify diagnosis",
            required_capabilities=["condition_counter", "valve_output"],
            supported_categories=[ModuleCategory("valve"), ModuleCategory("inout")],
            safety_class=SafetyClass.CAUTION,
            allowed_in_ci=True,
            can_run_parallel=False,
            parameters={"cc_param_id": 20094, "cc_readback_param_id": 20095, "toggle_cycles": 5},
        ),
        TestDefinition(
            test_id="remanent-params",
            name="Remanent Parameters",
            version="1.0.0",
            description="Write test values to remanent parameters, verify persistence after power cycle",
            required_capabilities=["remanent_params"],
            supported_categories=[
                ModuleCategory("input"),
                ModuleCategory("output"),
                ModuleCategory("inout"),
                ModuleCategory("valve"),
                ModuleCategory("bus"),
            ],
            safety_class=SafetyClass.SAFE,
            allowed_in_ci=True,
            can_run_parallel=False,
            parameters={"param_id_1": 20118, "param_id_2": 20119},
        ),
        TestDefinition(
            test_id="valve-toggle",
            name="Valve Toggle",
            version="1.0.0",
            description="Toggle all valve channels ON/OFF and verify state changes",
            required_capabilities=["valve_output"],
            supported_categories=[ModuleCategory("valve")],
            safety_class=SafetyClass.CAUTION,
            allowed_in_ci=True,
            can_run_parallel=False,
        ),
        TestDefinition(
            test_id="output-toggle",
            name="Output Toggle",
            version="1.0.0",
            description="Toggle all digital output channels ON/OFF and verify state changes",
            required_capabilities=["digital_output"],
            supported_categories=[ModuleCategory("output"), ModuleCategory("inout")],
            safety_class=SafetyClass.CAUTION,
            allowed_in_ci=True,
            can_run_parallel=False,
        ),
        TestDefinition(
            test_id="compare-topology",
            name="Topology Comparison",
            version="1.0.0",
            description="Compare stored topology against live hardware",
            required_capabilities=[],
            supported_categories=[ModuleCategory("bus")],
            safety_class=SafetyClass.SAFE,
            allowed_in_ci=True,
        ),
        TestDefinition(
            test_id="system-diagnosis",
            name="System Diagnosis",
            version="1.0.0",
            description="Read global system diagnosis registers",
            required_capabilities=["system_diagnosis"],
            supported_categories=[ModuleCategory("bus")],
            safety_class=SafetyClass.SAFE,
            allowed_in_ci=True,
        ),
    ]
