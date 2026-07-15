from __future__ import annotations

import unittest

from config_models import (
    BenchConfig,
    ModuleCategory,
    ModuleInstance,
    ModuleTypeDefinition,
    TestBenchMetadata,
    TestDefinition,
)
from resolver import TestResolver


class InstanceCapabilityResolutionTests(unittest.TestCase):
    def test_instance_capabilities_override_shared_type_capabilities(self) -> None:
        config = BenchConfig(
            test_bench=TestBenchMetadata(id="capability-test"),
            module_types={
                "digital-input": ModuleTypeDefinition(
                    module_code=1,
                    capabilities=["digital_input", "condition_counter"],
                ),
            },
            module_instances=[
                ModuleInstance(
                    instance_id="ordinary-input",
                    display_name="ordinary input",
                    module_code=1,
                    address=1,
                    category=ModuleCategory.INPUT,
                    module_type_ref="digital-input",
                    capabilities=["digital_input"],
                ),
                ModuleInstance(
                    instance_id="counter-input",
                    display_name="counter input",
                    module_code=1,
                    address=2,
                    category=ModuleCategory.INPUT,
                    module_type_ref="digital-input",
                    capabilities=["digital_input", "condition_counter"],
                ),
            ],
            test_definitions=[
                TestDefinition(
                    test_id="condition-counter",
                    name="Condition counter",
                    required_capabilities=["condition_counter"],
                    supported_categories=[ModuleCategory.INPUT],
                ),
            ],
        )

        plan = TestResolver().resolve(config)

        self.assertEqual(
            [instance.module_instance_id for instance in plan.instances],
            ["counter-input"],
        )

    def test_omitted_instance_capabilities_keep_legacy_type_fallback(self) -> None:
        config = BenchConfig(
            test_bench=TestBenchMetadata(id="legacy-capability-test"),
            module_types={
                "legacy": ModuleTypeDefinition(
                    module_code=2,
                    capabilities=["digital_input"],
                ),
            },
            module_instances=[
                ModuleInstance(
                    instance_id="legacy-input",
                    module_code=2,
                    address=1,
                    category=ModuleCategory.INPUT,
                    module_type_ref="legacy",
                ),
            ],
        )

        self.assertEqual(config.module_capabilities(config.module_instances[0]), {"digital_input"})


if __name__ == "__main__":
    unittest.main()
