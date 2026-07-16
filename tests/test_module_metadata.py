import json
import unittest
from pathlib import Path


class ModuleMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        metadata_path = Path(__file__).resolve().parents[1] / "module_metadata.json"
        cls.metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    def test_every_module_declares_capabilities(self) -> None:
        missing = [name for name, entry in self.metadata.items() if "capabilities" not in entry]
        self.assertEqual(missing, [])

    def test_vabx_parallel_interface_declares_valve_support(self) -> None:
        entry = self.metadata["VABX-A-P-EL-E12-API"]
        self.assertGreater(entry["valve_slots"], 0)
        self.assertIn("valve_output", entry["capabilities"])


if __name__ == "__main__":
    unittest.main()
