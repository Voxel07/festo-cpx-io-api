import unittest

from api import _preserve_mounted_valve_metadata
from config_io import load_bench_config


class GeneratedConfigMetadataTests(unittest.TestCase):
    def test_live_refresh_preserves_mounted_valves_and_slot_count(self) -> None:
        stored = load_bench_config("data/bench_config.json")
        live = stored.model_copy(deep=True)
        stored_vabx = next(
            module for module in stored.module_instances
            if module.display_name == "VABX-A-P-EL-E12-API"
        )
        live_vabx = next(
            module for module in live.module_instances
            if module.product_key == stored_vabx.product_key
        )

        live_vabx.mounted_valves = list(range(32))
        live_vabx.valve_slots = 32
        live.module_types[live_vabx.module_type_ref].valve_count = 32

        _preserve_mounted_valve_metadata(live, stored)

        self.assertEqual(live_vabx.mounted_valves, stored_vabx.mounted_valves)
        self.assertEqual(live_vabx.valve_slots, stored_vabx.valve_slots)
        self.assertEqual(
            live.module_types[live_vabx.module_type_ref].valve_count,
            stored_vabx.valve_slots,
        )


if __name__ == "__main__":
    unittest.main()
