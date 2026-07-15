"""Capability-based test resolver.

Matches generic test definitions against module instances on a test bench
by comparing declared capabilities.  Produces a resolved execution plan
that can be filtered and executed by the API-owned scheduler.

Usage::

    from resolver import TestResolver
    resolver = TestResolver()
    plan = resolver.resolve(bench_config, filters=TestFilter(test_id="connection-validation"))
    for instance in plan:
        print(instance.test_id, instance.module_instance_id)
"""

from __future__ import annotations

import hashlib
import importlib
import json
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from config_models import (
    AssignmentScope,
    BenchConfig,
    ChannelDefinition,
    ModuleInstance,
    SafetyClass,
    TestDefinition,
    create_basic_test_definitions,
    get_module_capabilities,
)


@lru_cache(maxsize=1)
def _cached_test_definitions() -> tuple[dict, ...]:
    test_defs = []
    tests_dir = Path(__file__).parent / "tests"
    if not tests_dir.exists():
        tests_dir = Path("tests")
        
    if not tests_dir.exists():
        return ()
        
    for file in tests_dir.glob("*.py"):
        if file.name in ("__init__.py", "_base.py", "test_api.py"):
            continue
        try:
            module_name = f"tests.{file.stem}"
            mod = importlib.import_module(module_name)
            if hasattr(mod, "TEST_DEFINITIONS"):
                test_defs.extend(mod.TEST_DEFINITIONS)
            elif hasattr(mod, "TEST_DEFINITION"):
                test_defs.append(mod.TEST_DEFINITION)
        except Exception as exc:
            print(f"Error loading test definition from {file}: {exc}")
            
    return tuple(test_defs)


def load_all_test_definitions() -> list[dict]:
    """Return isolated copies of cached test metadata discovered from modules."""
    return [dict(definition) for definition in _cached_test_definitions()]


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
    channel_mode: str | None = None
    wiring_id: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    is_negative_test: bool = False
    safety_class: SafetyClass = SafetyClass.SAFE
    can_run_parallel: bool = False
    source_instance_id: str | None = None
    target_instance_id: str | None = None

    @property
    def unique_id(self) -> str:
        """Generate a stable unique ID for this test instance."""
        parts = [self.test_id, self.module_instance_id]
        if self.channel_id:
            parts.append(self.channel_id)
        if self.channel_mode:
            parts.append(self.channel_mode)
        if self.wiring_id:
            parts.append(self.wiring_id)
        return "-".join(parts)


@dataclass(frozen=True)
class ConditionCounterRoute:
    """An output-to-CPX-AP-I-16* input route ready for a CC test."""

    wiring_id: str
    output_address: int
    output_channel: int
    counter_address: int
    counter_channel: int
    output_is_configurable: bool = False
    counter_is_configurable: bool = False

    @property
    def counter_instance(self) -> int:
        """The one-based parameter instance used by ``cpx_io``."""
        return self.counter_channel + 1


@dataclass
class ExecutionPlan:
    """The resolved execution plan — all test instances to execute."""

    test_bench_id: str
    test_bench_ip: str
    plan_id: str = ""
    created_at: str = ""
    instances: list[ResolvedTestInstance] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.instances)

    def filter_by_test_id(self, test_id: str) -> ExecutionPlan:
        return ExecutionPlan(
            test_bench_id=self.test_bench_id,
            test_bench_ip=self.test_bench_ip,
            plan_id=self.plan_id,
            created_at=self.created_at,
            instances=[i for i in self.instances if i.test_id == test_id],
        )

    def filter_by_safety_class(self, sc: SafetyClass) -> ExecutionPlan:
        return ExecutionPlan(
            test_bench_id=self.test_bench_id,
            test_bench_ip=self.test_bench_ip,
            plan_id=self.plan_id,
            created_at=self.created_at,
            instances=[i for i in self.instances if i.safety_class == sc],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "test_bench_id": self.test_bench_id,
            "test_bench_ip": self.test_bench_ip,
            "created_at": self.created_at,
            "total_instances": self.count,
            "execution_policy": "serial_per_bench",
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
                    "channel_mode": i.channel_mode,
                    "wiring_id": i.wiring_id,
                    "parameters": i.parameters,
                    "is_negative_test": i.is_negative_test,
                    "safety_class": i.safety_class.value,
                    "can_run_parallel": i.can_run_parallel,
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
        instances: list[ResolvedTestInstance] = []

        test_defs = config.test_definitions
        if not test_defs:
            raw_defs = load_all_test_definitions()
            test_defs = [TestDefinition.model_validate(d) for d in raw_defs]
        if not test_defs:
            test_defs = create_basic_test_definitions()

        for test_def in test_defs:
            singleton_emitted = False  # track for singleton tests
            for mod in config.module_instances:
                if not self._matches_category(test_def, mod):
                    continue
                # An explicit module override bypasses capability matching,
                # while category and exclusion rules still apply.
                if mod.compatible_tests_override:
                    if test_def.test_id not in mod.compatible_tests_override:
                        continue
                else:
                    if not self._matches_capabilities(test_def, config, mod):
                        continue
                if not self._matches_include_exclude(test_def, mod):
                    continue
                if (
                    test_def.target_module_instance_ids
                    and mod.instance_id not in test_def.target_module_instance_ids
                ):
                    continue

                is_negative = mod.is_negative_test_target

                scope = test_def.assignment_scope
                if test_def.required_wiring_type:
                    scope = AssignmentScope.WIRING
                elif test_def.required_channel_capabilities or test_def.required_channel_modes:
                    scope = AssignmentScope.CHANNEL

                # ── Singleton guard: system-wide tests emit exactly one instance ──
                if test_def.singleton and singleton_emitted:
                    continue

                # ── Module-level test (no channel/wiring needed) ──
                if scope in {AssignmentScope.MODULE, AssignmentScope.SYSTEM}:
                    instances.append(
                        ResolvedTestInstance(
                            test_id=test_def.test_id,
                            test_name=test_def.name,
                            test_version=test_def.version,
                            module_instance_id=mod.instance_id,
                            module_code=mod.module_code,
                            product_key=mod.product_key or "",
                            module_address=mod.address,
                            parameters=dict(test_def.parameters),
                            is_negative_test=is_negative,
                            safety_class=test_def.safety_class,
                            can_run_parallel=test_def.can_run_parallel,
                        )
                    )
                    singleton_emitted = True  # mark after first module-level instance

                # ── Wiring-based tests ──
                if scope == AssignmentScope.CHANNEL:
                    module_type = config.module_types.get(mod.module_type_ref)
                    if module_type is None:
                        continue
                    for channel in module_type.channels:
                        if not self._channel_satisfies(test_def, channel):
                            continue
                        if test_def.required_channel_modes:
                            modes = [
                                mode for mode in test_def.required_channel_modes
                                if mode in channel.supported_modes
                            ]
                        else:
                            modes = [channel.current_mode or channel.default_mode or ""]
                        for mode in modes or [""]:
                            parameters = dict(test_def.parameters)
                            parameters.update(
                                {"channel_index": channel.index, "channel_name": channel.name}
                            )
                            if mode:
                                parameters["channel_mode"] = mode
                            instances.append(
                                ResolvedTestInstance(
                                    test_id=test_def.test_id,
                                    test_name=test_def.name,
                                    test_version=test_def.version,
                                    module_instance_id=mod.instance_id,
                                    module_code=mod.module_code,
                                    product_key=mod.product_key or "",
                                    module_address=mod.address,
                                    channel_id=channel.name or str(channel.index),
                                    channel_mode=mode or None,
                                    parameters=parameters,
                                    is_negative_test=is_negative,
                                    safety_class=test_def.safety_class,
                                    can_run_parallel=test_def.can_run_parallel,
                                )
                            )

                if scope == AssignmentScope.WIRING:
                    for wire in config.wiring:
                        src_id = wire.source_instance_id
                        tgt_id = wire.target_instance_id
                        if mod.instance_id not in (src_id, tgt_id):
                            continue
                        if (
                            test_def.required_wiring_type
                            and wire.connection_type != test_def.required_wiring_type
                        ):
                            continue
                        bound_channel = (
                            wire.source_channel
                            if mod.instance_id == wire.source_instance_id
                            else wire.target_channel
                        )
                        instances.append(
                            ResolvedTestInstance(
                                test_id=test_def.test_id,
                                test_name=test_def.name,
                                test_version=test_def.version,
                                module_instance_id=mod.instance_id,
                                module_code=mod.module_code,
                                product_key=mod.product_key or "",
                                module_address=mod.address,
                                channel_id=bound_channel,
                                wiring_id=wire.id,
                                source_instance_id=src_id,
                                target_instance_id=tgt_id,
                                parameters=dict(test_def.parameters),
                                is_negative_test=is_negative,
                                safety_class=test_def.safety_class,
                                can_run_parallel=test_def.can_run_parallel,
                            )
                        )
                        singleton_emitted = True  # first wiring instance for singleton tests
                        if test_def.singleton:
                            break

        # ── Apply filters ──
        if filters:
            instances = self._apply_filters(instances, filters, config)

        # ── Deduplicate by unique_id ──
        seen: set[str] = set()
        deduped: list[ResolvedTestInstance] = []
        for inst in instances:
            if inst.unique_id not in seen:
                seen.add(inst.unique_id)
                deduped.append(inst)

        created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        digest_source = json.dumps(
            {
                "bench_id": config.test_bench.id,
                "bench_ip": config.test_bench.ip_address,
                "instances": [
                    {
                        "id": instance.unique_id,
                        "parameters": instance.parameters,
                        "negative": instance.is_negative_test,
                        "safety": instance.safety_class.value,
                    }
                    for instance in deduped
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return ExecutionPlan(
            test_bench_id=config.test_bench.id,
            test_bench_ip=config.test_bench.ip_address,
            plan_id="plan-" + hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:20],
            created_at=created_at,
            instances=deduped,
        )

    @staticmethod
    def resolve_condition_counter_routes(
        config: BenchConfig, module_address: int | None = None
    ) -> list[ConditionCounterRoute]:
        """Resolve and orient wiring suitable for condition-counter tests.

        CC parameters belong to CPX-AP-I-16* modules.  The configured wire may
        list that module at either endpoint.  On configurable DIO endpoints,
        ``port_directions`` in the bench configuration determines the source.
        """
        modules = {module.instance_id: module for module in config.module_instances}
        routes: list[ConditionCounterRoute] = []

        def channel_index(label: str) -> int:
            digits = "".join(ch for ch in str(label) if ch.isdigit())
            if not digits:
                raise ValueError(f"Invalid channel label: {label!r}")
            return int(digits)

        def capabilities(module: ModuleInstance) -> set[str]:
            module_type = config.module_types.get(module.module_type_ref)
            return set(module_type.capabilities if module_type else ())

        def is_counter_module(module: ModuleInstance) -> bool:
            return "condition_counter" in capabilities(module)

        def can_drive(module: ModuleInstance, label: str) -> bool:
            key = str(channel_index(label))
            if key in module.port_directions:
                return module.port_directions[key] is True
            return module.num_outputs > 0 or "digital_output" in capabilities(module)

        def can_count_input(module: ModuleInstance, label: str) -> bool:
            key = str(channel_index(label))
            if module.port_directions.get(key) is True:
                return False
            caps = capabilities(module)
            return module.num_inputs > 0 or bool({"digital_input", "configurable_io"} & caps)

        # Output variants count their own switching cycles and need no external
        # wire.  For configurable variants, bench_config selects output ports.
        for module in config.module_instances:
            if not is_counter_module(module):
                continue
            if module_address is not None and module.address != module_address:
                continue
            if module.port_directions:
                output_channels = sorted(
                    int(channel) for channel, output in module.port_directions.items() if output
                )
            elif module.num_outputs > 0 and module.num_inputs == 0:
                output_channels = list(range(module.num_outputs))
            else:
                output_channels = []
            for channel in output_channels:
                routes.append(ConditionCounterRoute(
                    wiring_id=f"internal:{module.instance_id}:{channel}",
                    output_address=module.address,
                    output_channel=channel,
                    counter_address=module.address,
                    counter_channel=channel,
                    output_is_configurable=module.num_inouts > 0,
                ))

        for wire in config.wiring:
            first = modules.get(wire.source_instance_id)
            second = modules.get(wire.target_instance_id)
            if first is None or second is None:
                continue
            orientations = (
                (first, wire.source_channel, second, wire.target_channel),
                (second, wire.target_channel, first, wire.source_channel),
            )
            for counter, counter_label, output, output_label in orientations:
                if (
                    not is_counter_module(counter)
                    or not can_count_input(counter, counter_label)
                    or not can_drive(output, output_label)
                ):
                    continue
                counter_key = str(channel_index(counter_label))
                if counter.port_directions.get(counter_key) is True:
                    continue
                if module_address is not None and counter.address != module_address:
                    continue
                routes.append(ConditionCounterRoute(
                    wiring_id=wire.id,
                    output_address=output.address,
                    output_channel=channel_index(output_label),
                    counter_address=counter.address,
                    counter_channel=channel_index(counter_label),
                    output_is_configurable=output.num_inouts > 0,
                    counter_is_configurable=counter.num_inouts > 0,
                ))
                break
        return routes

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
            # Fallback: infer capabilities dynamically if module_types is not in config
            mod_caps = set(get_module_capabilities(mod.display_name, mod.category.value))
        else:
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
    def _channel_satisfies(test: TestDefinition, channel: ChannelDefinition) -> bool:
        if not set(channel.capabilities).issuperset(test.required_channel_capabilities):
            return False
        if test.required_channel_modes and not any(
            mode in channel.supported_modes for mode in test.required_channel_modes
        ):
            return False
        return True

    @staticmethod
    def _apply_filters(
        instances: list[ResolvedTestInstance],
        filters: TestFilter,
        config: BenchConfig,
    ) -> list[ResolvedTestInstance]:
        result = instances
        if filters.test_id:
            tid = filters.test_id
            if tid == "validate-connections":
                result = [i for i in result if i.test_id == "connection-validation"]
            elif tid == "output-toggle":
                result = [i for i in result if i.test_id in ("output-toggle", "valve-toggle")]
            elif tid == "condition-counter-pc":
                # Power-cycle variant resolves the same instances as condition-counter;
                # the caller decides whether to use run_with_power_cycle.
                result = [i for i in result if i.test_id == "condition-counter"]
                for i in result:
                    i.parameters.setdefault("power_cycle", True)
            else:
                result = [i for i in result if i.test_id == tid]
        if filters.module_instance_id:
            result = [i for i in result if i.module_instance_id == filters.module_instance_id]
        if filters.module_code:
            result = [i for i in result if i.module_code == filters.module_code]
        if filters.product_key:
            result = [i for i in result if i.product_key == filters.product_key]
        if filters.capability:
            test_defs = config.test_definitions
            if not test_defs:
                raw_defs = load_all_test_definitions()
                test_defs = [TestDefinition.model_validate(d) for d in raw_defs]
            required_caps = {td.test_id: td.required_capabilities for td in test_defs}
            result = [i for i in result if filters.capability in required_caps.get(i.test_id, [])]
        if filters.safety_class:
            result = [i for i in result if i.safety_class == filters.safety_class]
        return result
