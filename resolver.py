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
import importlib.util
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config_models import (
    BenchConfig,
    ModuleInstance,
    SafetyClass,
    TestDefinition,
    get_module_capabilities,
)


def load_all_test_definitions() -> list[dict]:
    test_defs = []
    tests_dir = Path(__file__).parent / "tests"
    if not tests_dir.exists():
        tests_dir = Path("tests")
        
    if not tests_dir.exists():
        return []
        
    for file in tests_dir.glob("*.py"):
        if file.name in ("__init__.py", "_base.py"):
            continue
        try:
            module_name = f"tests.{file.stem}"
            spec = importlib.util.spec_from_file_location(module_name, file)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "TEST_DEFINITIONS"):
                    test_defs.extend(mod.TEST_DEFINITIONS)
                elif hasattr(mod, "TEST_DEFINITION"):
                    test_defs.append(mod.TEST_DEFINITION)
        except Exception as exc:
            print(f"Error loading test definition from {file}: {exc}")
            
    return test_defs


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
        instances: list[ResolvedTestInstance] = []

        test_defs = config.test_definitions
        if not test_defs:
            raw_defs = load_all_test_definitions()
            test_defs = [TestDefinition.model_validate(d) for d in raw_defs]

        for test_def in test_defs:
            singleton_emitted = False  # track for singleton tests
            for mod in config.module_instances:
                if not self._matches_category(test_def, mod):
                    continue
                if not self._matches_capabilities(test_def, config, mod):
                    continue
                if not self._matches_compatibility_pattern(test_def, mod):
                    continue
                if not self._matches_include_exclude(test_def, mod):
                    continue

                is_negative = mod.is_negative_test_target

                # ── Singleton guard: system-wide tests emit exactly one instance ──
                if test_def.singleton and singleton_emitted:
                    continue

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
                    singleton_emitted = True  # mark after first module-level instance

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
                        singleton_emitted = True  # first wiring instance for singleton tests

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

        return ExecutionPlan(
            test_bench_id=config.test_bench.id,
            test_bench_ip=config.test_bench.ip_address,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
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
            # Fallback: infer capabilities dynamically if module_types is not in config
            mod_caps = set(get_module_capabilities(mod.display_name, mod.category.value))
        else:
            mod_caps = set(type_def.capabilities)
        return mod_caps.issuperset(test.required_capabilities)

    @staticmethod
    def _matches_compatibility_pattern(test: TestDefinition, mod: ModuleInstance) -> bool:
        if not test.compatible_modules:
            return True
        return any(fnmatch.fnmatch(mod.display_name, pattern) for pattern in test.compatible_modules)

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

    def dry_run(self, config: BenchConfig, filters: TestFilter | None = None) -> ExecutionPlan:
        """Resolve tests without touching hardware (same as resolve, semantic alias)."""
        return self.resolve(config, filters)

    def export_plan(self, plan: ExecutionPlan, path: str) -> None:
        """Save the resolved execution plan to a JSON file."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(plan.to_dict(), f, indent=2)
