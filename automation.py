"""Scratch-style automation graph execution and PocketBase persistence.

The browser is only the editor/monitor.  A program executes in this process so
the scan loop talks directly to the shared :class:`HardwareInterface` without
adding one HTTP round-trip per I/O operation.
"""

from __future__ import annotations

import contextlib
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Any, Literal

import requests
from pydantic import BaseModel, Field, model_validator

from hal import HardwareInterface, ModuleInfo


BLOCK_TYPES = {
    "input",
    "temperature",
    "voltage",
    "pressure",
    "timer",
    "delay",
    "counter",
    "nand",
    "conversion",
    "output",
    "valve",
    "cylinder",
    "analog_in",
    "analog_out",
}


class AutomationPosition(BaseModel):
    x: float = 0
    y: float = 0


class AutomationNode(BaseModel):
    id: str = Field(min_length=1)
    type: str
    position: AutomationPosition = Field(default_factory=AutomationPosition)
    data: dict[str, Any] = Field(default_factory=dict)


class AutomationEdge(BaseModel):
    id: str = Field(min_length=1)
    source: str
    target: str
    sourceHandle: str | None = None
    targetHandle: str | None = None


class AutomationProgram(BaseModel):
    id: str | None = None
    name: str = Field("New automation", min_length=1, max_length=120)
    description: str = Field("", max_length=2000)
    version: str = "1.0"
    scan_interval_ms: int = Field(50, ge=10, le=2000)
    nodes: list[AutomationNode] = Field(default_factory=list)
    edges: list[AutomationEdge] = Field(default_factory=list)
    topology: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_graph(self) -> "AutomationProgram":
        node_ids = [node.id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("Automation node IDs must be unique")
        edge_ids = [edge.id for edge in self.edges]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("Automation edge IDs must be unique")
        known = set(node_ids)
        for node in self.nodes:
            if node.type not in BLOCK_TYPES:
                raise ValueError(f"Unsupported block type: {node.type}")
            if node.type in {"input", "temperature", "voltage", "pressure", "output", "valve", "analog_in", "analog_out"}:
                if "module_addr" not in node.data or "channel" not in node.data:
                    raise ValueError(
                        f"{node.type} block {node.id} needs module_addr and channel"
                    )
            if node.type == "timer":
                if int(node.data.get("initial_delay_ms", 1000)) < 0:
                    raise ValueError(f"Timer block {node.id} initial_delay_ms must be non-negative")
                if bool(node.data.get("repeat", False)) and int(node.data.get("interval_ms", 1000)) < 10:
                    raise ValueError(f"Timer block {node.id} interval_ms must be at least 10 when repeating")
            if node.type == "counter" and int(node.data.get("events_per_toggle", 1)) < 1:
                raise ValueError(f"Counter block {node.id} events_per_toggle must be at least 1")
            if node.type == "delay" and int(node.data.get("delay_ms", 1000)) < 0:
                raise ValueError(f"Delay block {node.id} delay_ms must be non-negative")
        for edge in self.edges:
            if edge.source not in known or edge.target not in known:
                raise ValueError(f"Edge {edge.id} references a missing node")
            if edge.source == edge.target:
                raise ValueError(f"Self-loop on node {edge.source} is not supported")
        return self


class AutomationStartRequest(BaseModel):
    program: AutomationProgram | None = None
    program_id: str | None = None
    target: Literal["real", "simulated"] = "real"

    @model_validator(mode="after")
    def select_source(self) -> "AutomationStartRequest":
        if (self.program is None) == (self.program_id is None):
            raise ValueError("Provide exactly one of program or program_id")
        return self


class SimulationInputRequest(BaseModel):
    node_id: str = Field(min_length=1)
    module_addr: int = Field(ge=0)
    channel: int = Field(ge=0)
    value: bool


class SimulationAnalogRequest(BaseModel):
    node_id: str = Field(min_length=1)
    module_addr: int = Field(ge=0)
    channel: int = Field(ge=0)
    value: float


class SimulatedHardware(HardwareInterface):
    """In-memory HAL used to run the exact automation engine without CPX hardware."""

    def __init__(self) -> None:
        self._inputs: dict[tuple[int, int], bool] = {}
        self._analogs: dict[tuple[int, int], float] = {}
        self._node_inputs: dict[str, bool] = {}
        self._node_analogs: dict[str, float] = {}
        self._outputs: dict[tuple[int, int], bool] = {}
        self._lock = threading.RLock()

    def connect(self, ip_address: str, timeout: float = 0) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def read_topology(self) -> list[ModuleInfo]:
        return []

    def read_input(self, address: int, channel: int) -> bool:
        with self._lock:
            return self._inputs.get((address, channel), False)

    def read_inputs(self, address: int, channels: list[int]) -> dict[int, bool]:
        with self._lock:
            return {channel: self._inputs.get((address, channel), False) for channel in channels}

    def read_analog(self, address: int, channel: int) -> float:
        with self._lock:
            return self._analogs.get((address, channel), 0.0)

    def read_analogs(self, address: int, channels: list[int]) -> dict[int, float]:
        with self._lock:
            return {channel: self._analogs.get((address, channel), 0.0) for channel in channels}

    def set_input(self, address: int, channel: int, value: bool) -> None:
        with self._lock:
            self._inputs[(address, channel)] = value

    def set_analog(self, address: int, channel: int, value: float) -> None:
        with self._lock:
            self._analogs[(address, channel)] = float(value)

    def set_node_input(self, node_id: str, value: bool) -> None:
        with self._lock:
            self._node_inputs[node_id] = bool(value)

    def set_node_analog(self, node_id: str, value: float) -> None:
        with self._lock:
            self._node_analogs[node_id] = float(value)

    def read_node_input(self, node_id: str, fallback: bool) -> bool:
        with self._lock:
            return self._node_inputs.get(node_id, fallback)

    def read_node_analog(self, node_id: str, fallback: float) -> float:
        with self._lock:
            return self._node_analogs.get(node_id, fallback)

    def write_output(self, address: int, channel: int, value: bool) -> None:
        with self._lock:
            self._outputs[(address, channel)] = value

    def write_channels(self, address: int, values: list[bool]) -> None:
        with self._lock:
            for channel, value in enumerate(values):
                self._outputs[(address, channel)] = bool(value)

    def reset_all_outputs(self) -> None:
        with self._lock:
            for key in self._outputs:
                self._outputs[key] = False

    def read_parameter(self, address: int, param_id: int, instance: int | None = None) -> Any:
        return 0

    def write_parameter(
        self, address: int, param_id: int, value: int, instance: int | None = None,
    ) -> None:
        return None

    def read_diagnosis(self, address: int) -> Any:
        return None

    def module_supports_channel_write(self, address: int) -> bool:
        return True

    def snapshot(self) -> dict[str, dict[str, bool | float]]:
        with self._lock:
            return {
                "inputs": {f"{address}:{channel}": value for (address, channel), value in self._inputs.items()},
                "analogs": {f"{address}:{channel}": value for (address, channel), value in self._analogs.items()},
                "node_inputs": dict(self._node_inputs),
                "node_analogs": dict(self._node_analogs),
                "outputs": {f"{address}:{channel}": value for (address, channel), value in self._outputs.items()},
            }


def _incoming_values(
    node_id: str,
    incoming: dict[str, list[AutomationEdge]],
    outputs: dict[str, dict[str, bool]],
) -> list[tuple[str, bool]]:
    values: list[tuple[str, bool]] = []
    for edge in incoming.get(node_id, []):
        source = outputs.get(edge.source, {})
        source_handle = edge.sourceHandle or "signal"
        value = source.get(source_handle, source.get("signal", False))
        values.append((edge.targetHandle or "signal", bool(value)))
    return values


def _execution_order(program: AutomationProgram) -> list[AutomationNode]:
    """Return a stable topological order, tolerating feedback around state blocks."""
    nodes = {node.id: node for node in program.nodes}
    indegree = {node.id: 0 for node in program.nodes}
    outgoing: dict[str, list[str]] = defaultdict(list)
    # Cylinders, delays and physical I/O are scan-state boundaries.  Ignoring
    # incoming dependencies for these nodes lets useful feedback graphs sort.
    boundaries = {"input", "temperature", "voltage", "timer", "delay", "output", "valve", "cylinder"}
    for edge in program.edges:
        if nodes[edge.target].type in boundaries:
            continue
        outgoing[edge.source].append(edge.target)
        indegree[edge.target] += 1
    queue = deque(node.id for node in program.nodes if indegree[node.id] == 0)
    ordered: list[AutomationNode] = []
    seen: set[str] = set()
    while queue:
        node_id = queue.popleft()
        if node_id in seen:
            continue
        seen.add(node_id)
        ordered.append(nodes[node_id])
        for target in outgoing.get(node_id, []):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    ordered.extend(node for node in program.nodes if node.id not in seen)
    # Sources first, action/state blocks after combinatorial logic.
    rank = {"input": 0, "analog_in": 0, "conversion": 0, "temperature": 0, "voltage": 0, "timer": 0, "nand": 1,
            "pressure": 2, "counter": 2, "delay": 2,
            "output": 3, "valve": 3, "analog_out": 3, "cylinder": 4}
    return sorted(ordered, key=lambda node: rank.get(node.type, 9))


class AutomationEngine:
    """Single-program cyclic executor for the shared Modbus connection."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._hardware: HardwareInterface | None = None
        self._program: AutomationProgram | None = None
        self._status: dict[str, Any] = {
            "running": False,
            "target": None,
            "program_id": None,
            "program_name": None,
            "cycle_count": 0,
            "last_cycle_ms": None,
            "last_error": None,
            "node_states": {},
        }
        self._runtime: dict[str, dict[str, Any]] = defaultdict(dict)
        self._owned_outputs: set[tuple[int, int]] = set()
        self._physical_states: dict[tuple[int, int], bool] = {}

    @property
    def running(self) -> bool:
        with self._lock:
            return bool(self._status["running"])

    def start(
        self,
        program: AutomationProgram,
        hardware: HardwareInterface,
        *,
        target: Literal["real", "simulated"] = "real",
    ) -> None:
        self.stop(reset_outputs=True)
        # Establish a deterministic, de-energized starting point.  The shared
        # connection is exclusive while automation is active.
        hardware.reset_all_outputs()
        with self._lock:
            self._program = program
            self._hardware = hardware
            self._runtime = defaultdict(dict)
            self._owned_outputs = set()
            self._physical_states = {}
            self._status = {
                "running": True,
                "target": target,
                "program_id": program.id,
                "program_name": program.name,
                "cycle_count": 0,
                "last_cycle_ms": None,
                "last_error": None,
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "node_states": {},
            }
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name=f"automation-{program.name[:24]}",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, reset_outputs: bool = True) -> None:
        thread = self._thread
        self._stop.set()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=3)
        if reset_outputs:
            self._reset_owned_outputs()
        with self._lock:
            self._status["running"] = False
            self._thread = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def _reset_owned_outputs(self) -> None:
        hardware = self._hardware
        if hardware is None:
            return
        for address, channel in list(self._owned_outputs):
            with contextlib.suppress(Exception):
                hardware.write_output(address, channel, False)
        self._owned_outputs.clear()
        for key in self._physical_states:
            self._physical_states[key] = False

    def _run(self) -> None:
        assert self._program is not None
        interval = self._program.scan_interval_ms / 1000
        next_cycle = time.monotonic()
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                outputs = self._scan(started)
                elapsed_ms = (time.monotonic() - started) * 1000
                with self._lock:
                    self._status["cycle_count"] += 1
                    self._status["last_cycle_ms"] = round(elapsed_ms, 3)
                    self._status["node_states"] = outputs
                next_cycle += interval
                wait = max(0, next_cycle - time.monotonic())
                if self._stop.wait(wait):
                    break
                if wait == 0:
                    next_cycle = time.monotonic()
        except Exception as exc:  # hardware faults must stop the automation safely
            with self._lock:
                self._status["last_error"] = str(exc)
        finally:
            self._reset_owned_outputs()
            with self._lock:
                self._status["running"] = False

    def _scan(self, now: float) -> dict[str, dict[str, bool | float]]:
        assert self._program is not None
        assert self._hardware is not None
        incoming: dict[str, list[AutomationEdge]] = defaultdict(list)
        for edge in self._program.edges:
            incoming[edge.target].append(edge)
        previous_outputs: dict[str, dict[str, bool]] = {
            node_id: dict(state.get("outputs", {}))
            for node_id, state in self._runtime.items()
        }
        outputs: dict[str, dict[str, bool | float]] = dict(previous_outputs)

        requested_inputs: dict[int, set[int]] = defaultdict(set)
        for graph_node in self._program.nodes:
            if graph_node.type == "input":
                requested_inputs[int(graph_node.data["module_addr"])].add(
                    int(graph_node.data["channel"])
                )
        input_values: dict[tuple[int, int], bool] = {}
        for address, channels in requested_inputs.items():
            values = self._hardware.read_inputs(address, sorted(channels))
            input_values.update({
                (address, channel): bool(value) for channel, value in values.items()
            })

        requested_analogs: dict[int, set[int]] = defaultdict(set)
        for graph_node in self._program.nodes:
            if graph_node.type in {"temperature", "voltage", "pressure", "analog_in"}:
                requested_analogs[int(graph_node.data["module_addr"])].add(
                    int(graph_node.data["channel"])
                )
        analog_values: dict[tuple[int, int], float] = {}
        for address, channels in requested_analogs.items():
            values = self._hardware.read_analogs(address, sorted(channels))
            analog_values.update({
                (address, channel): float(value) for channel, value in values.items()
            })

        for node in _execution_order(self._program):
            state = self._runtime[node.id]
            values = _incoming_values(node.id, incoming, outputs)  # type: ignore[arg-type]
            signal = any(value for _, value in values)

            if node.type == "input":
                raw = bool(input_values[(
                    int(node.data["module_addr"]), int(node.data["channel"])
                )])
                if isinstance(self._hardware, SimulatedHardware):
                    raw = self._hardware.read_node_input(node.id, raw)
                debounce_s = max(0, int(node.data.get("debounce_ms", 0))) / 1000
                if raw != state.get("candidate", raw):
                    state["candidate"] = raw
                    state["candidate_since"] = now
                if "stable" not in state:
                    state["stable"] = raw
                    state["candidate"] = raw
                    state["candidate_since"] = now
                elif raw != state["stable"] and now - state["candidate_since"] >= debounce_s:
                    state["previous_stable"] = state["stable"]
                    state["stable"] = raw
                stable = bool(state["stable"])
                previous = bool(state.pop("previous_stable", stable))
                mode = node.data.get("trigger", "rising")
                event = {
                    "rising": stable and not previous,
                    "falling": previous and not stable,
                    "change": stable != previous,
                    "level_high": stable,
                    "level_low": not stable,
                }.get(mode, stable)
                result: dict[str, bool | float] = {"signal": event, "state": stable}

            elif node.type in {"temperature", "voltage", "pressure"}:
                # Accept an upstream numeric value from an analog_in block if wired,
                # otherwise read directly from the hardware analog input.
                upstream_values = [
                    float(outputs.get(edge.source, {}).get("value", 0.0))
                    for edge in incoming.get(node.id, [])
                    if (outputs.get(edge.source, {}).get("_is_analog"))
                ]
                if upstream_values:
                    value = upstream_values[0]
                    raw_value = value
                else:
                    raw_value = analog_values[(
                        int(node.data["module_addr"]), int(node.data["channel"])
                    )]
                    if isinstance(self._hardware, SimulatedHardware):
                        raw_value = self._hardware.read_node_analog(node.id, raw_value)
                    scale = float(node.data.get("scale", 0.1 if node.type == "temperature" else 10 / 27648))
                    offset = float(node.data.get("offset", 0.0))
                    value = raw_value * scale + offset
                limit = float(node.data.get("limit", 25.0 if node.type == "temperature" else 5.0 if node.type == "voltage" else 6.0))
                hysteresis = max(0.0, float(node.data.get("hysteresis", 0.1 if node.type == "pressure" else 0.0)))
                was_active = bool(state.get("active", False))
                active = value >= (limit - hysteresis if was_active else limit)
                state["active"] = active
                result = {
                    "signal": active and signal if node.type == "pressure" else active,
                    "state": active,
                    "value": round(value, 4),
                    "raw_value": round(raw_value, 4),
                    "limit": limit,
                }

            elif node.type == "timer":
                initial_delay_s = max(0, int(node.data.get("initial_delay_ms", 1000))) / 1000
                interval_s = max(0.01, int(node.data.get("interval_ms", 1000)) / 1000)
                repeat = bool(node.data.get("repeat", False))
                if "scheduled" not in state:
                    state["scheduled"] = True
                    state["next_due"] = now + initial_delay_s
                    state["fired_count"] = 0
                next_due = state.get("next_due")
                fired = next_due is not None and now >= float(next_due)
                if fired:
                    state["fired_count"] = int(state.get("fired_count", 0)) + 1
                    if repeat:
                        overdue = max(0.0, now - float(next_due))
                        skipped_intervals = int(overdue // interval_s)
                        state["next_due"] = float(next_due) + (skipped_intervals + 1) * interval_s
                    else:
                        state["next_due"] = None
                next_due = state.get("next_due")
                remaining_ms = max(0.0, (float(next_due) - now) * 1000) if next_due is not None else 0.0
                result = {
                    "signal": bool(fired),
                    "pending": next_due is not None,
                    "remaining_ms": round(remaining_ms, 1),
                    "fired_count": int(state.get("fired_count", 0)),
                }

            elif node.type == "nand":
                source_values = [value for _, value in values]
                result = {"signal": not (bool(source_values) and all(source_values))}

            elif node.type == "counter":
                previous = bool(state.get("input", False))
                incoming_event = signal and not previous
                events_per_toggle = max(1, int(node.data.get("events_per_toggle", 1)))
                total_count = int(state.get("total_count", 0))
                if incoming_event:
                    total_count += 1
                fired = incoming_event and total_count % events_per_toggle == 0
                state["input"] = signal
                state["total_count"] = total_count
                result = {
                    "signal": bool(fired),
                    "count": total_count % events_per_toggle,
                    "total_count": total_count,
                    "events_per_toggle": events_per_toggle,
                }

            elif node.type == "delay":
                previous = bool(state.get("input", False))
                delay_s = max(0, int(node.data.get("delay_ms", 1000))) / 1000
                if signal and not previous and not state.get("latched", False):
                    state["due"] = now + delay_s
                if state.get("due") is not None and now >= state["due"]:
                    state["due"] = None
                    state["latched"] = True
                state["input"] = signal
                result = {
                    "signal": bool(state.get("latched", False)),
                    "state": bool(state.get("latched", False)),
                    "pending": state.get("due") is not None,
                }

            elif node.type in {"output", "valve"}:
                previous_trigger = bool(state.get("trigger", False))
                action = node.data.get("action", "toggle")
                address = int(node.data["module_addr"])
                channel = int(node.data["channel"])
                output_key = (address, channel)
                current = bool(self._physical_states.get(output_key, False))
                should_write = False
                if action == "follow":
                    desired = signal
                    should_write = desired != current or "value" not in state
                elif signal and not previous_trigger:
                    desired = {"on": True, "off": False}.get(action, not current)
                    should_write = True
                else:
                    desired = current
                if should_write:
                    self._hardware.write_output(address, channel, bool(desired))
                    self._owned_outputs.add(output_key)
                    self._physical_states[output_key] = bool(desired)
                state["trigger"] = signal
                current = bool(self._physical_states.get(output_key, False))
                result = {
                    "signal": current,
                    "state": current,
                    "extend": current,
                    "retract": not current,
                }

            elif node.type == "cylinder":
                extend_values = [value for handle, value in values if handle in {"extend", "signal"}]
                retract_values = [value for handle, value in values if handle == "retract"]
                command_extend = any(extend_values) and not any(retract_values)
                travel_s = max(0.05, float(node.data.get("travel_time_s", 1.0)))
                last = float(state.get("updated_at", now))
                position = float(state.get("position", 0.0))
                delta = max(0, now - last) / travel_s
                position = min(1.0, position + delta) if command_extend else max(0.0, position - delta)
                was_extended = bool(state.get("extended", False))
                was_retracted = bool(state.get("retracted", True))
                extended = position >= 0.999
                retracted = position <= 0.001
                state.update({
                    "position": position,
                    "updated_at": now,
                    "extended": extended,
                    "retracted": retracted,
                })
                result = {
                    "signal": extended,
                    "extended": extended,
                    "retracted": retracted,
                    "extended-event": extended and not was_extended,
                    "retracted-event": retracted and not was_retracted,
                    "position": round(position, 4),
                }

            elif node.type == "analog_in":
                raw_value = analog_values[(
                    int(node.data["module_addr"]), int(node.data["channel"])
                )]
                if isinstance(self._hardware, SimulatedHardware):
                    raw_value = self._hardware.read_node_analog(node.id, raw_value)
                scale = float(node.data.get("scale", 1.0))
                offset = float(node.data.get("offset", 0.0))
                value = raw_value * scale + offset
                result = {
                    "signal": True,
                    "value": round(value, 6),
                    "raw_value": round(raw_value, 6),
                    "_is_analog": True,
                }

            elif node.type == "conversion":
                upstream_numeric = [
                    float(outputs.get(edge.source, {}).get("value", 0.0))
                    for edge in incoming.get(node.id, [])
                    if outputs.get(edge.source, {}).get("_is_analog")
                ]
                input_value = upstream_numeric[0] if upstream_numeric else 0.0
                scale = float(node.data.get("scale", 1.0))
                offset = float(node.data.get("offset", 0.0))
                value = input_value * scale + offset
                result = {
                    "signal": True,
                    "value": round(value, 6),
                    "input_value": round(input_value, 6),
                    "_is_analog": True,
                }

            elif node.type == "analog_out":
                # Accept a numeric value from an upstream analog block.
                upstream_numeric = [
                    float(outputs.get(edge.source, {}).get("value", 0.0))
                    for edge in incoming.get(node.id, [])
                    if outputs.get(edge.source, {})
                ]
                out_value = upstream_numeric[0] if upstream_numeric else 0.0
                scale = float(node.data.get("scale", 1.0))
                offset = float(node.data.get("offset", 0.0))
                raw_out = (out_value - offset) / scale if scale != 0 else 0.0
                address = int(node.data["module_addr"])
                channel = int(node.data["channel"])
                self._hardware.write_output(address, channel, bool(raw_out > 0))
                result = {
                    "signal": bool(raw_out > 0),
                    "value": round(out_value, 6),
                    "raw_value": round(raw_out, 6),
                }

            else:
                result = {"signal": False}

            state["outputs"] = result
            outputs[node.id] = result
        return outputs


class AutomationProgramStore:
    """PocketBase-backed program CRUD with a clearly marked memory fallback."""

    collection = "festo_automation_programs"

    def __init__(self) -> None:
        self._memory: dict[str, AutomationProgram] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _pb() -> tuple[str, dict[str, str]]:
        from pocketbase_logger import PB_URL, _headers

        return PB_URL, _headers()

    @staticmethod
    def _payload(program: AutomationProgram) -> dict[str, Any]:
        graph = program.model_dump(exclude={"id", "topology"})
        return {
            "name": program.name,
            "description": program.description,
            "version": program.version,
            "scan_interval_ms": program.scan_interval_ms,
            "graph": graph,
            "topology": program.topology or {},
        }

    @staticmethod
    def _from_record(record: dict[str, Any]) -> AutomationProgram:
        graph = dict(record.get("graph") or {})
        graph.update({
            "id": record.get("id"),
            "name": record.get("name") or graph.get("name") or "Automation",
            "description": record.get("description") or graph.get("description") or "",
            "version": record.get("version") or graph.get("version") or "1.0",
            "scan_interval_ms": record.get("scan_interval_ms") or graph.get("scan_interval_ms") or 50,
            "topology": record.get("topology") or None,
        })
        return AutomationProgram.model_validate(graph)

    def list(self) -> tuple[list[AutomationProgram], Literal["pocketbase", "memory"]]:
        try:
            base, headers = self._pb()
            response = requests.get(
                f"{base}/api/collections/{self.collection}/records",
                params={"sort": "-updated", "perPage": 200},
                headers=headers,
                timeout=(1, 2),
            )
            response.raise_for_status()
            programs: list[AutomationProgram] = []
            for item in response.json().get("items", []):
                try:
                    programs.append(self._from_record(item))
                except Exception as exc:
                    # One invalid record must not hide every other PocketBase
                    # program from the editor selector.
                    print(
                        f"[Automation] Skipping invalid program {item.get('id', '?')}: {exc}",
                        flush=True,
                    )
            return programs, "pocketbase"
        except Exception:
            with self._lock:
                return list(self._memory.values()), "memory"

    def get(self, program_id: str) -> AutomationProgram | None:
        try:
            base, headers = self._pb()
            response = requests.get(
                f"{base}/api/collections/{self.collection}/records/{program_id}",
                headers=headers,
                timeout=(1, 2),
            )
            response.raise_for_status()
            return self._from_record(response.json())
        except Exception:
            with self._lock:
                return self._memory.get(program_id)

    def save(self, program: AutomationProgram) -> tuple[AutomationProgram, str]:
        payload = self._payload(program)
        try:
            base, headers = self._pb()
            if program.id and not program.id.startswith("local_"):
                response = requests.patch(
                    f"{base}/api/collections/{self.collection}/records/{program.id}",
                    json=payload,
                    headers=headers,
                    timeout=(1, 2),
                )
            else:
                response = requests.post(
                    f"{base}/api/collections/{self.collection}/records",
                    json=payload,
                    headers=headers,
                    timeout=(1, 2),
                )
            response.raise_for_status()
            saved = self._from_record(response.json())
            with self._lock:
                if program.id:
                    self._memory.pop(program.id, None)
            return saved, "pocketbase"
        except Exception:
            program_id = program.id or f"local_{uuid.uuid4().hex}"
            saved = program.model_copy(update={"id": program_id})
            with self._lock:
                self._memory[program_id] = saved
            return saved, "memory"

    def delete(self, program_id: str) -> str:
        try:
            base, headers = self._pb()
            response = requests.delete(
                f"{base}/api/collections/{self.collection}/records/{program_id}",
                headers=headers,
                timeout=(1, 2),
            )
            response.raise_for_status()
            persistence = "pocketbase"
        except Exception:
            persistence = "memory"
        with self._lock:
            self._memory.pop(program_id, None)
        return persistence


automation_engine = AutomationEngine()
automation_store = AutomationProgramStore()
simulation_hardware = SimulatedHardware()
