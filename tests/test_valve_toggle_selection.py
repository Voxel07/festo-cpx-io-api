from __future__ import annotations

import unittest
from unittest.mock import patch

from config_models import (
    BenchConfig,
    ModuleCategory,
    ModuleInstance,
    ModuleTypeDefinition,
    TestBenchMetadata,
    TestDefinition,
)
from hal import ModuleInfo
from resolver import TestResolver
from tests.test_valve_toggle import run


class FakeHardware:
    def __init__(self, module: ModuleInfo) -> None:
        self.module = module
        self.writes: list[tuple[int, int, bool]] = []

    def read_topology(self) -> list[ModuleInfo]:
        return [self.module]

    def write_output(self, address: int, channel: int, value: bool) -> None:
        self.writes.append((address, channel, value))


def make_config(
    *,
    category: ModuleCategory = ModuleCategory.VALVE,
    mounted_valves: list[int] | None = None,
) -> BenchConfig:
    return BenchConfig(
        test_bench=TestBenchMetadata(id="valve-test"),
        module_types={
            "valve": ModuleTypeDefinition(
                module_code=8224,
                capabilities=["valve_output"],
                num_outputs=32,
                valve_count=16,
                channels_per_valve=2,
            ),
        },
        module_instances=[
            ModuleInstance(
                instance_id="valve-11",
                display_name="VMPAL-EPL-AP",
                module_code=8224,
                product_key="valve-11",
                address=11,
                category=category,
                module_type_ref="valve",
                capabilities=["digital_output", "valve_output"],
                mounted_valves=[] if mounted_valves is None else mounted_valves,
            ),
        ],
    )


class ValveToggleSelectionTests(unittest.TestCase):
    def test_only_mounted_valve_channels_are_toggled(self) -> None:
        config = make_config(mounted_valves=[1, 3])
        hw = FakeHardware(ModuleInfo(
            name="VMPAL-EPL-AP",
            module_code=8224,
            product_key="valve-11",
            address=11,
            num_outputs=32,
        ))

        with patch("tests.test_valve_toggle.time.sleep"):
            results = run(hw, bench_config=config, module_address=11)

        high_channels = [channel for _, channel, value in hw.writes if value]
        self.assertEqual(high_channels, [2, 3, 6, 7])
        self.assertEqual(results[0]["mounted_valves"], [1, 3])
        self.assertEqual(results[0]["total_channels"], 4)
        self.assertTrue(results[0]["passed"])

    def test_no_mounted_valves_is_a_detailed_skip(self) -> None:
        config = make_config(mounted_valves=[])
        hw = FakeHardware(ModuleInfo(
            name="VAEM-V-S8RS2",
            module_code=8224,
            product_key="valve-11",
            address=11,
            num_outputs=32,
        ))

        results = run(hw, bench_config=config, module_address=11)

        self.assertEqual(hw.writes, [])
        self.assertTrue(results[0]["skipped"])
        self.assertEqual(results[0]["note"], "No valves are configured as mounted")

    def test_interface_with_valve_capability_is_resolved(self) -> None:
        config = make_config(category=ModuleCategory.INTERFACE, mounted_valves=[0])
        config.test_definitions = [TestDefinition(
            test_id="valve-toggle",
            name="Valve Toggle",
            required_capabilities=["valve_output"],
            supported_categories=[ModuleCategory.VALVE, ModuleCategory.INTERFACE],
        )]

        plan = TestResolver().resolve(config)

        self.assertEqual(len(plan.instances), 1)
        self.assertEqual(plan.instances[0].module_address, 11)


if __name__ == "__main__":
    unittest.main()
