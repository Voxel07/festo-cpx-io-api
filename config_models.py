"""Pydantic models for test bench configuration validation.

Provides typed models for all configuration concepts: test bench metadata,
module instances, module type definitions, channel definitions, wiring,
test definitions, and UI visualization metadata.

Usage::

    from config_models import BenchConfig
    config = BenchConfig.model_validate_json(jsonc_content)
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    Field,
    model_validator,
    StringConstraints,
)


# ─── Enums ────────────────────────────────────────────────────────────────────


class ModuleCategory(str, Enum):
    INPUT = "input"
    OUTPUT = "output"
    INOUT = "inout"
    BUS = "bus"
    VALVE = "valve"


class ExpectedState(str, Enum):
    PRESENT = "present"
    OPTIONAL = "optional"
    ABSENT = "absent"


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


# ─── Module Type Definition ───────────────────────────────────────────────────


class ModuleTypeDefinition(BaseModel):
    """Static definition of a module type — capabilities, channel layout, image."""

    module_code: int = Field(..., description="Numeric module code")
    product_family: str = Field("", description="e.g. 'CPX-AP-A', 'CPX-AP-I', 'VABX'")
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
    expected_state: ExpectedState = Field(ExpectedState.PRESENT)
    firmware_version: str | None = Field(None)
    serial_number: str | None = Field(None)
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
        description="0-based indices of mounted valve slots (VABX only)",
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
    hotspot_radius: float = 10.0


class UIVisualizationMetadata(BaseModel):
    module_positions: list[UIModulePosition] = Field(default_factory=list)
    channel_anchors: list[UIChannelAnchor] = Field(default_factory=list)


# ─── Top-level Bench Configuration ────────────────────────────────────────────


class BenchConfig(BaseModel):
    """Complete test bench configuration."""

    schema_version: str = Field("1.0", description="Configuration schema version")
    test_bench: TestBenchMetadata
    module_types: dict[str, ModuleTypeDefinition] = Field(
        default_factory=dict,
        description="Module type definitions keyed by type reference string",
    )
    module_instances: list[ModuleInstance] = Field(..., min_length=1)
    wiring: list[WiringConnection] = Field(default_factory=list)
    test_definitions: list[TestDefinition] = Field(default_factory=list)
    ui_metadata: UIVisualizationMetadata = Field(default_factory=UIVisualizationMetadata)

    # ── Validators ─────────────────────────────────────────────────────────

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
