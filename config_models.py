"""Pydantic models for test bench configuration validation.

Provides typed models for all configuration concepts: test bench metadata,
module instances, module type definitions, channel definitions, wiring,
test definitions, and UI visualization metadata.

Usage::

    from config_models import BenchConfig
    config = BenchConfig.model_validate_json(jsonc_content)
"""

from __future__ import annotations

import re

from enum import Enum
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    Field,
    model_validator,
    StringConstraints,
    ConfigDict,
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


# ─── Module Type Definition ───────────────────────────────────────────────────


class ModuleTypeDefinition(BaseModel):
    """Static definition of a module type — capabilities, channel layout, image."""

    module_code: int = Field(..., description="Numeric module code")
    capabilities: list[str] = Field(
        default_factory=list,
        description="Supported capabilities: digital_input, digital_output, condition_counter, etc.",
    )
    num_inputs: int = Field(0, ge=0)
    num_outputs: int = Field(0, ge=0)
    num_configurable: int = Field(0, ge=0, description="Configurable in/out channels")
    valve_count: int = Field(0, ge=0, description="Number of valve slots (VABX only)")
    channels: list[ChannelDefinition] = Field(default_factory=list)
    image_asset: str = Field("", description="SVG/PNG filename for UI")


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
    product_key: str = Field(..., description="Unique product key (serial-like)")
    address: int = Field(..., ge=0, description="Bus address (0-based position)")
    category: ModuleCategory = Field(..., description="Input / Output / InOut / Bus / Valve")
    module_type_ref: str = Field(
        "",
        description="Key into module_types map for this module's type definition",
    )
    firmware_version: str | None = Field(None)
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
    compatible_modules: list[str] = Field(
        default_factory=list,
        description="Glob patterns of module names compatible with this test",
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
    hotspot_radius: float = 10.0


class UIVisualizationMetadata(BaseModel):
    module_positions: list[UIModulePosition] = Field(default_factory=list)
    channel_anchors: list[UIChannelAnchor] = Field(default_factory=list)


def get_module_capabilities(display_name: str, category: str) -> list[str]:
    name_up = display_name.upper()
    caps = []
    
    # Check category/name for input
    if category == "input" or "DI" in name_up or "DIO" in name_up or "DIDO" in name_up or "VABX" in name_up:
        caps.append("digital_input")
        
    # Check category/name for output
    if category == "output" or "DO" in name_up or "DIO" in name_up or "DIDO" in name_up or "VABX" in name_up:
        caps.append("digital_output")
        
    # Check category/name for valve
    if category == "valve" or "VABX" in name_up or "VMPAL" in name_up or "VAEM" in name_up:
        caps.extend(["valve_output"])
        
    # Condition counter & Remanent params support
    if any(x in name_up for x in ("DI", "DO", "DIO", "HDO", "AI", "IOL", "VABX")):
        caps.extend(["condition_counter", "remanent_params"])
        
    # System diagnosis support for bus/interface modules
    if category == "bus" or any(x in name_up for x in ("EP", "EC", "PN", "PB", "EPLI")):
        caps.append("system_diagnosis")
        
    return list(set(caps))


def infer_type_definition_from_instance(mod: ModuleInstance) -> ModuleTypeDefinition:
    name = mod.display_name.upper()
    code = mod.module_code
    category = mod.category
    
    num_in = 0
    num_out = 0
    num_io = 0
    valve_count = 0
    
    if "VABX" in name or "VMPAL" in name or "VAEM" in name or category == ModuleCategory.VALVE:
        num_in = 8 if "VABX" in name else 0
        num_out = 8
        valve_count = 16
    else:
        # Check for configurable DIO / DIDO channels BEFORE separate DI / DO:
        # '4DIDO' and '16DIO' have fully configurable ports (num_io), whereas
        # '4DI8DO' has separate fixed-direction ports (num_in + num_out).
        dido_match = re.search(r'(\d+)(?:DIDO|DIO)', name)
        if dido_match:
            num_io = int(dido_match.group(1))
        else:
            # Match input channels (DI or AI)
            di_match = re.search(r'(\d+)(?:DI|AI)', name)
            if di_match:
                num_in = int(di_match.group(1))

            # Match output channels (DO, HDO, or AO)
            do_match = re.search(r'(\d+)(?:DO|HDO|AO)', name)
            if do_match:
                num_out = int(do_match.group(1))
        
    caps = get_module_capabilities(mod.display_name, category.value)
    
    channels = []
    max_ch = max(num_in, num_out, num_io, 8)
    for ch_idx in range(max_ch):
        ch_caps = []
        if num_out > 0 or category == ModuleCategory.VALVE:
            ch_caps.append("digital_output")
        if num_in > 0:
            ch_caps.append("digital_input")
        if num_io > 0:
            ch_caps.append("configurable_io")
            
        channels.append(
            ChannelDefinition(
                index=ch_idx,
                name=f"X{ch_idx}",
                capabilities=ch_caps,
            )
        )
        
    return ModuleTypeDefinition(
        module_code=code,
        capabilities=caps,
        num_inputs=num_in,
        num_outputs=num_out,
        num_configurable=num_io,
        valve_count=valve_count,
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
        """Warn about tests referencing nonexistent capabilities (non-fatal)."""
        return self  # Soft check — resolver handles missing capabilities at runtime

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
        """Warn about duplicate product keys (non-fatal by default)."""
        keys = [m.product_key for m in self.module_instances if m.product_key]
        seen: set[str] = set()
        dupes = {k for k in keys if k in seen or seen.add(k)}
        if dupes:
            import warnings
            warnings.warn(f"Duplicate product keys found: {sorted(dupes)}", UserWarning)
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
                    valid_names = ch_names | ch_indices
                    if w.source_channel not in valid_names:
                        raise ValueError(
                            f"Wiring '{w.id}': source channel '{w.source_channel}' "
                            f"not found on module '{src_mod.instance_id}'"
                        )
            # Target checks
            tgt_mod = next((m for m in self.module_instances if m.instance_id == w.target_instance_id), None)
            if tgt_mod:
                type_def = self.module_types.get(tgt_mod.module_type_ref)
                if type_def and type_def.channels:
                    ch_names = {c.name for c in type_def.channels}
                    ch_indices = {str(c.index) for c in type_def.channels}
                    valid_names = ch_names | ch_indices
                    if w.target_channel not in valid_names:
                        raise ValueError(
                            f"Wiring '{w.id}': target channel '{w.target_channel}' "
                            f"not found on module '{tgt_mod.instance_id}'"
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

        def infer_cat(name: str, is_valve: bool, num_in: int, num_out: int, num_io: int) -> ModuleCategory:
            name_up = name.upper()
            if is_valve or "VABX-A-S-BV-V" in name_up or "VMPAL" in name_up or "VAEM" in name_up:
                return ModuleCategory.VALVE
            if any(x in name_up for x in ("EP", "EC", "PN", "PB", "EPLI")) or "bus" in name_up:
                return ModuleCategory.BUS
            if num_io > 0 or (num_in > 0 and num_out > 0):
                return ModuleCategory.INOUT
            if num_out > 0:
                return ModuleCategory.OUTPUT
            if num_in > 0:
                return ModuleCategory.INPUT
            return ModuleCategory.BUS

        def infer_caps(name: str, num_in: int, num_out: int, num_io: int) -> list[str]:
            caps = []
            name_up = name.upper()
            if num_in > 0:
                caps.append("digital_input")
            if num_out > 0:
                caps.append("digital_output")
            if num_io > 0:
                caps.append("configurable_io")
            if "VABX" in name_up:
                caps.extend(["valve_output", "condition_counter", "remanent_params"])
            if any(x in name_up for x in ("DI", "DO", "DIO", "HDO", "AI", "IOL", "VABX")):
                caps.append("condition_counter")
                caps.append("remanent_params")
            if any(x in name_up for x in ("EP", "EC", "PN", "PB")):
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

            cat = infer_cat(name, m.is_valve, m.num_inputs, m.num_outputs, m.num_inouts)
            caps = infer_caps(name, m.num_inputs, m.num_outputs, m.num_inouts)

            # Build channel definitions dynamically
            channels = []
            for ch_idx in range(max(m.num_inputs, m.num_outputs, m.num_inouts, 8)):
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
                    capabilities=caps,
                    num_inputs=m.num_inputs,
                    num_outputs=m.num_outputs,
                    num_configurable=m.num_inouts,
                    valve_count=16 if cat == ModuleCategory.VALVE else 0,
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
                    mounted_valves=list(range(16)) if cat == ModuleCategory.VALVE else [],
                    num_inputs=m.num_inputs,
                    num_outputs=m.num_outputs,
                    num_inouts=m.num_inouts,
                )
            )

        return cls(
            schema_version="1.0",
            test_bench=meta,
            module_types={},
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
