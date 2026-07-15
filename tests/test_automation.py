from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import Mock, patch

from pydantic import ValidationError

from automation import AutomationEngine, AutomationProgram, AutomationProgramStore, SimulatedHardware
from hal import CpxApHardware, HardwareInterface, ModuleInfo


class FakeHardware(HardwareInterface):
    def __init__(self) -> None:
        self.inputs: dict[tuple[int, int], bool] = {}
        self.analogs: dict[tuple[int, int], float] = {}
        self.outputs: dict[tuple[int, int], bool] = {}
        self.writes: list[tuple[int, int, bool]] = []
        self.bulk_reads: list[tuple[int, tuple[int, ...]]] = []

    def connect(self, ip_address: str, timeout: float = 0) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def read_topology(self) -> list[ModuleInfo]:
        return []

    def read_input(self, address: int, channel: int) -> bool:
        return self.inputs.get((address, channel), False)

    def read_inputs(self, address: int, channels: list[int]) -> dict[int, bool]:
        self.bulk_reads.append((address, tuple(channels)))
        return {channel: self.read_input(address, channel) for channel in channels}

    def read_analog(self, address: int, channel: int) -> float:
        return self.analogs.get((address, channel), 0.0)

    def read_analogs(self, address: int, channels: list[int]) -> dict[int, float]:
        return {channel: self.read_analog(address, channel) for channel in channels}

    def write_output(self, address: int, channel: int, value: bool) -> None:
        self.outputs[(address, channel)] = value
        self.writes.append((address, channel, value))

    def write_channels(self, address: int, values: list[bool]) -> None:
        for channel, value in enumerate(values):
            self.write_output(address, channel, value)

    def reset_all_outputs(self) -> None:
        for key in self.outputs:
            self.outputs[key] = False

    def read_parameter(self, address: int, param_id: int, instance: int | None = None) -> Any:
        return 0

    def write_parameter(self, address: int, param_id: int, value: int, instance: int | None = None) -> None:
        pass

    def read_diagnosis(self, address: int) -> Any:
        return None

    def module_supports_channel_write(self, address: int) -> bool:
        return True


def program(nodes: list[dict], edges: list[dict]) -> AutomationProgram:
    return AutomationProgram(name="test", nodes=nodes, edges=edges, scan_interval_ms=20)


class AutomationEngineTests(unittest.TestCase):
  def test_program_list_skips_an_invalid_record_without_hiding_valid_records(self) -> None:
    valid = program(
        [{"id": "nand", "type": "nand", "data": {}}],
        [],
    )
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "items": [
            {"id": "invalid", "name": "Old graph", "graph": {"nodes": [{"id": "old", "type": "and", "data": {}}], "edges": []}},
            {"id": "valid", "name": "Current graph", "graph": valid.model_dump(exclude={"id", "topology"})},
        ],
    }
    store = AutomationProgramStore()
    with patch.object(store, "_pb", return_value=("http://pocketbase", {})), patch.object(store._session, "get", return_value=response):
      programs, persistence = store.list()

    self.assertEqual(persistence, "pocketbase")
    self.assertEqual([item.id for item in programs], ["valid"])

  def test_hardware_reads_modules_without_bulk_read_helper(self) -> None:
    class SingleChannelModule:
      position = 3

      def read_channel(self, channel: int) -> int:
        return {0: 0, 1: 1, 2: 1234}[channel]

    hardware = CpxApHardware()
    hardware._modules = [SingleChannelModule()]
    self.assertEqual(hardware.read_inputs(3, [0, 1]), {0: False, 1: True})
    self.assertEqual(hardware.read_analogs(3, [2]), {2: 1234.0})

  def test_one_shot_timer_triggers_an_output_once(self) -> None:
    hardware = FakeHardware()
    graph = program(
        [
            {"id": "clock", "type": "timer", "data": {"initial_delay_ms": 100, "repeat": False}},
            {"id": "q", "type": "output", "data": {"module_addr": 5, "channel": 0, "action": "toggle"}},
        ],
        [{"id": "clock-q", "source": "clock", "sourceHandle": "signal", "target": "q"}],
    )
    engine = AutomationEngine()
    engine._program = graph
    engine._hardware = hardware
    self.assertIs(engine._scan(1.0)["clock"]["signal"], False)
    self.assertIs(engine._scan(1.09)["clock"]["signal"], False)
    fired = engine._scan(1.11)
    self.assertIs(fired["clock"]["signal"], True)
    self.assertIs(hardware.outputs[(5, 0)], True)
    engine._scan(1.30)
    self.assertEqual(hardware.writes, [(5, 0, True)])

  def test_repeating_timer_keeps_a_stable_cadence(self) -> None:
    hardware = FakeHardware()
    graph = program(
        [{"id": "clock", "type": "timer", "data": {"initial_delay_ms": 50, "interval_ms": 200, "repeat": True}}],
        [],
    )
    engine = AutomationEngine()
    engine._program = graph
    engine._hardware = hardware
    self.assertIs(engine._scan(5.0)["clock"]["signal"], False)
    first = engine._scan(5.06)
    self.assertIs(first["clock"]["signal"], True)
    self.assertEqual(first["clock"]["fired_count"], 1)
    self.assertIs(engine._scan(5.20)["clock"]["signal"], False)
    second = engine._scan(5.26)
    self.assertIs(second["clock"]["signal"], True)
    self.assertEqual(second["clock"]["fired_count"], 2)

  def test_simulated_hardware_keeps_virtual_io_state(self) -> None:
    hardware = SimulatedHardware()
    hardware.set_input(3, 7, True)
    hardware.set_analog(4, 0, 27648)
    hardware.set_node_input("input-a", True)
    hardware.set_node_analog("analog-a", 123.5)
    self.assertEqual(hardware.read_inputs(3, [0, 7]), {0: False, 7: True})
    hardware.write_output(5, 1, True)
    self.assertEqual(hardware.snapshot(), {
        "inputs": {"3:7": True},
        "analogs": {"4:0": 27648.0},
        "node_inputs": {"input-a": True},
        "node_analogs": {"analog-a": 123.5},
        "outputs": {"5:1": True},
    })
    hardware.reset_all_outputs()
    self.assertIs(hardware.snapshot()["outputs"]["5:1"], False)

  def test_simulated_inputs_on_the_same_channel_are_overridden_per_node(self) -> None:
    hardware = SimulatedHardware()
    hardware.set_node_input("a", True)
    hardware.set_node_input("b", False)
    graph = program(
        [
            {"id": "a", "type": "input", "data": {"module_addr": 3, "channel": 0, "trigger": "level_high"}},
            {"id": "b", "type": "input", "data": {"module_addr": 3, "channel": 0, "trigger": "level_high"}},
        ],
        [],
    )
    engine = AutomationEngine()
    engine._program = graph
    engine._hardware = hardware

    state = engine._scan(1.0)
    self.assertIs(state["a"]["state"], True)
    self.assertIs(state["b"]["state"], False)

  def test_temperature_and_voltage_limits_emit_boolean_signals(self) -> None:
    hardware = FakeHardware()
    hardware.analogs[(4, 0)] = 249
    hardware.analogs[(4, 1)] = 13824
    graph = program(
        [
            {"id": "temp", "type": "temperature", "data": {"module_addr": 4, "channel": 0, "scale": 0.1, "limit": 25.0, "hysteresis": 1.0}},
            {"id": "volt", "type": "voltage", "data": {"module_addr": 4, "channel": 1, "scale": 10 / 27648, "limit": 5.0}},
        ],
        [],
    )
    engine = AutomationEngine()
    engine._program = graph
    engine._hardware = hardware
    below = engine._scan(1.0)
    self.assertIs(below["temp"]["signal"], False)
    self.assertIs(below["volt"]["signal"], True)
    hardware.analogs[(4, 0)] = 250
    reached = engine._scan(1.1)
    self.assertIs(reached["temp"]["signal"], True)
    hardware.analogs[(4, 0)] = 245
    held_by_hysteresis = engine._scan(1.2)
    self.assertIs(held_by_hysteresis["temp"]["signal"], True)
    hardware.analogs[(4, 0)] = 239
    self.assertIs(engine._scan(1.3)["temp"]["signal"], False)

  def test_rising_input_delay_toggles_output_without_blocking(self) -> None:
    hardware = FakeHardware()
    graph = program(
        [
            {"id": "i", "type": "input", "data": {"module_addr": 3, "channel": 2, "trigger": "rising"}},
            {"id": "d", "type": "delay", "data": {"delay_ms": 100}},
            {"id": "q", "type": "output", "data": {"module_addr": 5, "channel": 1, "action": "toggle"}},
        ],
        [
            {"id": "e1", "source": "i", "sourceHandle": "signal", "target": "d", "targetHandle": "trigger"},
            {"id": "e2", "source": "d", "sourceHandle": "signal", "target": "q", "targetHandle": "trigger"},
        ],
    )
    engine = AutomationEngine()
    engine._program = graph
    engine._hardware = hardware

    engine._scan(1.0)
    hardware.inputs[(3, 2)] = True
    engine._scan(1.02)
    self.assertEqual(hardware.writes, [])
    pending = engine._scan(1.08)
    self.assertIs(pending["d"]["pending"], True)
    engine._scan(1.13)
    self.assertIs(hardware.outputs[(5, 1)], True)
    held = engine._scan(1.20)
    self.assertIs(held["d"]["signal"], True)
    # The delayed event stays latched, while the action sees only one rising edge.
    self.assertEqual(hardware.writes, [(5, 1, True)])

  def test_counter_emits_only_on_every_configured_event(self) -> None:
    hardware = FakeHardware()
    graph = program(
        [
            {"id": "i", "type": "input", "data": {"module_addr": 3, "channel": 0, "trigger": "rising"}},
            {"id": "count", "type": "counter", "data": {"events_per_toggle": 3}},
        ],
        [{"id": "i-count", "source": "i", "target": "count"}],
    )
    engine = AutomationEngine()
    engine._program = graph
    engine._hardware = hardware
    engine._scan(1.0)
    events: list[bool] = []
    for index in range(3):
      hardware.inputs[(3, 0)] = True
      events.append(engine._scan(1.1 + index * 0.2)["count"]["signal"])
      hardware.inputs[(3, 0)] = False
      engine._scan(1.2 + index * 0.2)
    self.assertEqual(events, [False, False, True])

  def test_pressure_gate_forwards_signal_only_at_configured_pressure(self) -> None:
    hardware = FakeHardware()
    hardware.analogs[(4, 0)] = 13824
    graph = program(
        [
            {"id": "i", "type": "input", "data": {"module_addr": 3, "channel": 0, "trigger": "level_high"}},
            {"id": "pressure", "type": "pressure", "data": {"module_addr": 4, "channel": 0, "scale": 10 / 27648, "limit": 6.0}},
        ],
        [{"id": "i-pressure", "source": "i", "sourceHandle": "state", "target": "pressure"}],
    )
    engine = AutomationEngine()
    engine._program = graph
    engine._hardware = hardware
    self.assertIs(engine._scan(1.0)["pressure"]["signal"], False)
    hardware.inputs[(3, 0)] = True
    self.assertIs(engine._scan(1.1)["pressure"]["signal"], False)
    hardware.analogs[(4, 0)] = 20000
    reached = engine._scan(1.2)["pressure"]
    self.assertIs(reached["state"], True)
    self.assertIs(reached["signal"], True)


  def test_virtual_cylinder_emits_end_position_event(self) -> None:
    hardware = FakeHardware()
    hardware.inputs[(3, 0)] = True
    graph = program(
        [
            {"id": "i", "type": "input", "data": {"module_addr": 3, "channel": 0, "trigger": "level_high"}},
            {"id": "c", "type": "cylinder", "data": {"travel_time_s": 1.0}},
        ],
        [{"id": "e", "source": "i", "sourceHandle": "state", "target": "c", "targetHandle": "extend"}],
    )
    engine = AutomationEngine()
    engine._program = graph
    engine._hardware = hardware
    first = engine._scan(10.0)
    self.assertIs(first["c"]["retracted"], True)
    final = engine._scan(11.1)
    self.assertIs(final["c"]["extended"], True)
    self.assertIs(final["c"]["extended-event"], True)


  def test_graph_rejects_missing_edge_endpoint(self) -> None:
    with self.assertRaisesRegex(ValidationError, "missing node"):
        program(
            [{"id": "i", "type": "input", "data": {"module_addr": 3, "channel": 0}}],
            [{"id": "e", "source": "i", "target": "missing"}],
        )


  def test_stop_resets_only_outputs_owned_by_the_program(self) -> None:
    hardware = FakeHardware()
    engine = AutomationEngine()
    engine._hardware = hardware
    engine._owned_outputs = {(4, 1), (4, 2)}
    hardware.outputs = {(4, 1): True, (4, 2): True, (9, 0): True}
    engine.stop(reset_outputs=True)
    self.assertIs(hardware.outputs[(4, 1)], False)
    self.assertIs(hardware.outputs[(4, 2)], False)
    self.assertIs(hardware.outputs[(9, 0)], True)

  def test_inputs_on_one_module_share_one_process_image_read(self) -> None:
    hardware = FakeHardware()
    graph = program(
        [
            {"id": "i0", "type": "input", "data": {"module_addr": 3, "channel": 0}},
            {"id": "i7", "type": "input", "data": {"module_addr": 3, "channel": 7}},
        ],
        [],
    )
    engine = AutomationEngine()
    engine._program = graph
    engine._hardware = hardware
    engine._scan(1.0)
    self.assertEqual(hardware.bulk_reads, [(3, (0, 7))])

  def test_nand_is_false_only_when_both_inputs_are_high(self) -> None:
    hardware = FakeHardware()
    graph = program(
        [
            {"id": "a", "type": "input", "data": {"module_addr": 3, "channel": 0, "trigger": "level_high"}},
            {"id": "b", "type": "input", "data": {"module_addr": 3, "channel": 1, "trigger": "level_high"}},
            {"id": "nand", "type": "nand", "data": {}},
        ],
        [
            {"id": "a-nand", "source": "a", "sourceHandle": "state", "target": "nand", "targetHandle": "input-a"},
            {"id": "b-nand", "source": "b", "sourceHandle": "state", "target": "nand", "targetHandle": "input-b"},
        ],
    )
    engine = AutomationEngine()
    engine._program = graph
    engine._hardware = hardware

    self.assertIs(engine._scan(1.0)["nand"]["signal"], True)
    hardware.inputs[(3, 0)] = True
    self.assertIs(engine._scan(1.1)["nand"]["signal"], True)
    hardware.inputs[(3, 1)] = True
    self.assertIs(engine._scan(1.2)["nand"]["signal"], False)

  def test_conversion_scales_an_incoming_analog_value(self) -> None:
    hardware = FakeHardware()
    hardware.analogs[(4, 0)] = 2.5
    graph = program(
        [
            {"id": "analog", "type": "analog_in", "data": {"module_addr": 4, "channel": 0}},
            {"id": "convert", "type": "conversion", "data": {"scale": 9 / 5, "offset": 32}},
        ],
        [{"id": "analog-convert", "source": "analog", "sourceHandle": "value", "target": "convert", "targetHandle": "value"}],
    )
    engine = AutomationEngine()
    engine._program = graph
    engine._hardware = hardware

    converted = engine._scan(1.0)["convert"]
    self.assertEqual(converted["input_value"], 2.5)
    self.assertEqual(converted["value"], 36.5)
    self.assertIs(converted["_is_analog"], True)

  def test_nand_drives_follow_output(self) -> None:
    hardware = FakeHardware()
    hardware.inputs[(3, 0)] = True
    hardware.inputs[(3, 1)] = True
    graph = program(
        [
            {"id": "a", "type": "input", "data": {"module_addr": 3, "channel": 0}},
            {"id": "b", "type": "input", "data": {"module_addr": 3, "channel": 1}},
            {"id": "nand", "type": "nand", "data": {}},
            {"id": "q", "type": "output", "data": {"module_addr": 5, "channel": 0, "action": "follow"}},
        ],
        [
            {"id": "a-nand", "source": "a", "sourceHandle": "state", "target": "nand", "targetHandle": "input-a"},
            {"id": "b-nand", "source": "b", "sourceHandle": "state", "target": "nand", "targetHandle": "input-b"},
            {"id": "nand-q", "source": "nand", "sourceHandle": "signal", "target": "q"},
        ],
    )
    engine = AutomationEngine()
    engine._program = graph
    engine._hardware = hardware
    state = engine._scan(1.0)
    self.assertIs(state["nand"]["signal"], False)
    self.assertIs(hardware.outputs[(5, 0)], False)
    hardware.inputs[(3, 1)] = False
    state = engine._scan(1.1)
    self.assertIs(state["nand"]["signal"], True)
    self.assertIs(hardware.outputs[(5, 0)], True)

  def test_action_blocks_for_one_valve_share_physical_state(self) -> None:
    hardware = FakeHardware()
    graph = program(
        [
            {"id": "on_event", "type": "input", "data": {"module_addr": 3, "channel": 0, "trigger": "rising"}},
            {"id": "off_event", "type": "input", "data": {"module_addr": 3, "channel": 1, "trigger": "rising"}},
            {"id": "valve_on", "type": "valve", "data": {"module_addr": 5, "channel": 0, "action": "on"}},
            {"id": "valve_off", "type": "valve", "data": {"module_addr": 5, "channel": 0, "action": "off"}},
        ],
        [
            {"id": "on", "source": "on_event", "target": "valve_on"},
            {"id": "off", "source": "off_event", "target": "valve_off"},
        ],
    )
    engine = AutomationEngine()
    engine._program = graph
    engine._hardware = hardware
    engine._scan(1.0)
    hardware.inputs[(3, 0)] = True
    on_state = engine._scan(1.1)
    self.assertIs(on_state["valve_on"]["extend"], True)
    hardware.inputs[(3, 1)] = True
    engine._scan(1.2)
    shared_state = engine._scan(1.3)
    self.assertIs(hardware.outputs[(5, 0)], False)
    self.assertIs(shared_state["valve_on"]["extend"], False)
