"""Capability-based test resolver.

Matches generic test definitions against module instances on a test bench
by comparing declared capabilities.  Produces a resolved execution plan
that can be exported, filtered, and passed to the test runner.

Usage::

    from resolver import TestResolver
    resolver = TestResolver()
    plan = resolver.resolve(bench_config, filters=TestFilter(test_id="connection-validation"))
    for instance in plan:
        print(instance.test_id, instance.module_instance_id)
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config_models import (
    BenchConfig,
    ConnectionType,
    ExpectedState,
    ModuleInstance,
    SafetyClass,
    TestDefinition,
    WiringConnection,
)


# ─── Data types ───────────────────────────────────────────────────────────────


@dataclass
class ResolvedTestInstance:
    """A concrete test instance bound to a specific module/channel/connection."""

    test_id: str
    test_name: str
    test_version: str
    module_instance_id: str
    module_code: int
    product_key: str
    module_address: int
    channel_id: str | None = None
    wiring_id: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    is_negative_test: bool = False
    safety_class: SafetyClass = SafetyClass.SAFE
    source_instance_id: str | None = None
    target_instance_id: str | None = None

    @property
    def unique_id(self) -> str:
        """Generate a stable unique ID for this test instance."""
        parts = [self.test_id, self.module_instance_id]
        if self.channel_id:
            parts.append(self.channel_id)
        if self.wiring_id:
            parts.append(self.wiring_id)
        return "-".join(parts)


@dataclass
class ExecutionPlan:
    """The resolved execution plan — all test instances to execute."""

    test_bench_id: str
    test_bench_ip: str
    created_at: str = ""
    instances: list[ResolvedTestInstance] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.instances)

    def filter_by_test_id(self, test_id: str) -> ExecutionPlan:
        return ExecutionPlan(
            test_bench_id=self.test_bench_id,
            test_bench_ip=self.test_bench_ip,
            created_at=self.created_at,
            instances=[i for i in self.instances if i.test_id == test_id],
        )

    def filter_by_safety_class(self, sc: SafetyClass) -> ExecutionPlan:
        return ExecutionPlan(
            test_bench_id=self.test_bench_id,
            test_bench_ip=self.test_bench_ip,
            created_at=self.created_at,
            instances=[i for i in self.instances if i.safety_class == sc],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_bench_id": self.test_bench_id,
            "test_bench_ip": self.test_bench_ip,
            "created_at": self.created_at,
            "total_instances": self.count,
            "instances": [
                {
                    "unique_id": i.unique_id,
                    "test_id": i.test_id,
                    "test_name": i.test_name,
                    "test_version": i.test_version,
                    "module_instance_id": i.module_instance_id,
                    "module_code": i.module_code,
                    "product_key": i.product_key,
                    "module_address": i.module_address,
                    "channel_id": i.channel_id,
                    "wiring_id": i.wiring_id,
                    "parameters": i.parameters,
                    "is_negative_test": i.is_negative_test,
                    "safety_class": i.safety_class.value,
                }
                for i in self.instances
            ],
        }


@dataclass
class TestFilter:
    """Optional filter criteria for the resolver."""

    test_id: str | None = None
    module_instance_id: str | None = None
    module_code: int | None = None
    product_key: str | None = None
    capability: str | None = None
    safety_class: SafetyClass | None = None


# ─── Resolver ─────────────────────────────────────────────────────────────────


class TestResolver:
    """Matches test definitions to module instances based on capabilities."""

    def resolve(
        self,
        config: BenchConfig,
        filters: TestFilter | None = None,
    ) -> ExecutionPlan:
        """Produce an execution plan from a bench configuration.

        For each (test_definition × module_instance) pair, checks:
        1. Module category matches supported_categories
        2. Module capabilities satisfy required_capabilities
        3. Include/exclude rules by module_code and product_key
        4. Wiring requirements (for connection-based tests)
        5. Negative-test module handling
        """
        import time as _time

        instances: list[ResolvedTestInstance] = []

        # Build lookup: address → instance_id
        addr_to_id: dict[int, str] = {m.address: m.instance_id for m in config.module_instances}

        for test_def in config.test_definitions:
            for mod in config.module_instances:
                if not self._matches_category(test_def, mod):
                    continue
                if not self._matches_capabilities(test_def, config, mod):
                    continue
                if not self._matches_include_exclude(test_def, mod):
                    continue

                is_negative = mod.is_negative_test_target

                # ── Module-level test (no channel/wiring needed) ──
                if not test_def.required_wiring_type:
                    instances.append(
                        ResolvedTestInstance(
                            test_id=test_def.test_id,
                            test_name=test_def.name,
                            test_version=test_def.version,
                            module_instance_id=mod.instance_id,
                            module_code=mod.module_code,
                            product_key=mod.product_key,
                            module_address=mod.address,
                            parameters=dict(test_def.parameters),
                            is_negative_test=is_negative,
                            safety_class=test_def.safety_class,
                        )
                    )

                # ── Channel-level tests ──
                type_def = config.module_types.get(mod.module_type_ref)
                if type_def and type_def.channels:
                    for ch in type_def.channels:
                        if self._channel_satisfies(test_def, ch.capabilities):
                            instances.append(
                                ResolvedTestInstance(
                                    test_id=test_def.test_id,
                                    test_name=test_def.name,
                                    test_version=test_def.version,
                                    module_instance_id=mod.instance_id,
                                    module_code=mod.module_code,
                                    product_key=mod.product_key,
                                    module_address=mod.address,
                                    channel_id=ch.name or f"ch{ch.index}",
                                    parameters=dict(test_def.parameters),
                                    is_negative_test=is_negative,
                                    safety_class=test_def.safety_class,
                                )
                            )

                # ── Wiring-based tests ──
                if test_def.required_wiring_type:
                    for wire in config.wiring:
                        src_id = wire.source_instance_id
                        tgt_id = wire.target_instance_id
                        if mod.instance_id not in (src_id, tgt_id):
                            continue
                        if wire.connection_type != test_def.required_wiring_type:
                            continue
                        instances.append(
                            ResolvedTestInstance(
                                test_id=test_def.test_id,
                                test_name=test_def.name,
                                test_version=test_def.version,
                                module_instance_id=mod.instance_id,
                                module_code=mod.module_code,
                                product_key=mod.product_key,
                                module_address=mod.address,
                                wiring_id=wire.id,
                                source_instance_id=src_id,
                                target_instance_id=tgt_id,
                                parameters=dict(test_def.parameters),
                                is_negative_test=is_negative,
                                safety_class=test_def.safety_class,
                            )
                        )

        # ── Apply filters ──
        if filters:
            instances = self._apply_filters(instances, filters)

        # ── Deduplicate by unique_id ──
        seen: set[str] = set()
        deduped: list[ResolvedTestInstance] = []
        for inst in instances:
            if inst.unique_id not in seen:
                seen.add(inst.unique_id)
                deduped.append(inst)

        return ExecutionPlan(
            test_bench_id=config.test_bench.id,
            test_bench_ip=config.test_bench.ip_address,
            created_at=_time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
            instances=deduped,
        )

    # ── Matching helpers ──────────────────────────────────────────────────

    @staticmethod
    def _matches_category(test: TestDefinition, mod: ModuleInstance) -> bool:
        if not test.supported_categories:
            return True
        return mod.category in test.supported_categories

    @staticmethod
    def _matches_capabilities(
        test: TestDefinition,
        config: BenchConfig,
        mod: ModuleInstance,
    ) -> bool:
        if not test.required_capabilities:
            return True
        type_def = config.module_types.get(mod.module_type_ref)
        if type_def is None:
            return False
        mod_caps = set(type_def.capabilities)
        return mod_caps.issuperset(test.required_capabilities)

    @staticmethod
    def _matches_include_exclude(test: TestDefinition, mod: ModuleInstance) -> bool:
        # Exclude overrides include
        if test.exclude_module_codes and mod.module_code in test.exclude_module_codes:
            return False
        if test.exclude_product_keys and mod.product_key in test.exclude_product_keys:
            return False
        if mod.excluded_tests and test.test_id in mod.excluded_tests:
            return False

        if test.include_module_codes and mod.module_code not in test.include_module_codes:
            return False
        if test.include_product_keys and mod.product_key not in test.include_product_keys:
            return False

        # Module-level overrides
        if mod.compatible_tests_override:
            return test.test_id in mod.compatible_tests_override

        return True

    @staticmethod
    def _channel_satisfies(test: TestDefinition, channel_caps: list[str]) -> bool:
        if not test.required_capabilities:
            return False  # Don't generate channel tests without requirements
        return set(channel_caps).issuperset(test.required_capabilities)

    @staticmethod
    def _apply_filters(
        instances: list[ResolvedTestInstance],
        filters: TestFilter,
    ) -> list[ResolvedTestInstance]:
        result = instances
        if filters.test_id:
            result = [i for i in result if i.test_id == filters.test_id]
        if filters.module_instance_id:
            result = [i for i in result if i.module_instance_id == filters.module_instance_id]
        if filters.module_code:
            result = [i for i in result if i.module_code == filters.module_code]
        if filters.product_key:
            result = [i for i in result if i.product_key == filters.product_key]
        if filters.capability:
            # Soft filter — keeps instances that might match
            pass
        if filters.safety_class:
            result = [i for i in result if i.safety_class == filters.safety_class]
        return result

    def dry_run(self, config: BenchConfig, filters: TestFilter | None = None) -> ExecutionPlan:
        """Resolve tests without touching hardware (same as resolve, semantic alias)."""
        return self.resolve(config, filters)

    def export_plan(self, plan: ExecutionPlan, path: str) -> None:
        """Save the resolved execution plan to a JSON file."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(plan.to_dict(), f, indent=2)


# ─── Convenience ──────────────────────────────────────────────────────────────


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
            required_capabilities=["digital_output", "digital_input"],
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
            test_id="compare-topology",
            name="Topology Comparison",
            version="1.0.0",
            description="Compare stored topology against live hardware",
            required_capabilities=[],
            supported_categories=[],
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


# Import needed for default test definitions
from config_models import ModuleCategory  # noqa: E402, F811
