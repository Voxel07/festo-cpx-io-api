from __future__ import annotations

import asyncio
from types import SimpleNamespace

import api
from hal import CpxApHardware


class FakeModule:
    def __init__(
        self,
        direction: str,
        channel_count: int = 1,
        has_direction_parameter: bool = False,
    ) -> None:
        self.position = 7
        outputs = [SimpleNamespace(direction=direction) for _ in range(channel_count)]
        if direction == "inout":
            inputs = outputs
            inouts = outputs
        elif has_direction_parameter:
            inputs = [SimpleNamespace(direction="in") for _ in range(channel_count)]
            inouts = []
        else:
            inputs = []
            inouts = []
        self.channels = SimpleNamespace(
            inputs=inputs,
            outputs=outputs,
            inouts=inouts,
        )
        parameters = {}
        if has_direction_parameter:
            parameters[20145] = SimpleNamespace(
                parameter_instances={
                    "FirstIndex": 0,
                    "NumberOfInstances": channel_count,
                }
            )
        self.module_dicts = SimpleNamespace(parameters=parameters)
        self.parameter_writes: list[tuple[int, bool, int | None]] = []
        self.channel_writes: list[tuple[int, bool]] = []

    def write_module_parameter(
        self, parameter: int, value: bool, instances: int | None = None,
    ) -> None:
        self.parameter_writes.append((parameter, value, instances))

    def write_channel(self, channel: int, value: bool) -> None:
        self.channel_writes.append((channel, value))


class FakeManager:
    def __init__(self, hw: CpxApHardware) -> None:
        self.hw = hw
        self.is_connected = True
        self.ip_address = "192.168.0.11"

    def get_hw(self) -> CpxApHardware:
        return self.hw


def _call_set_output(
    monkeypatch, module: FakeModule, value: bool, channel: str = "X0",
):
    hw = CpxApHardware()
    hw._modules = [module]
    monkeypatch.setattr(api, "get_connection_manager", lambda: FakeManager(hw))
    monkeypatch.setattr(api, "_IO_AUTO_RESET_S", 0)
    monkeypatch.setattr(api, "_DIO_DIRECTION_SETTLE_S", 0)
    request = api.SetOutputRequest(
        ip_address="192.168.0.11",
        module_addr=7,
        channel=channel,
        value=value,
    )
    return asyncio.run(api.io_set_output(request))


def test_set_output_configures_dio_channel_with_zero_based_instance(monkeypatch) -> None:
    module = FakeModule("inout")

    response = _call_set_output(monkeypatch, module, True)

    assert module.parameter_writes == [(20145, True, 0)]
    assert module.channel_writes == [(0, True)]
    assert b'"channels_written":[0]' in response.body


def test_clear_output_restores_dio_channel_to_input(monkeypatch) -> None:
    module = FakeModule("inout")

    _call_set_output(monkeypatch, module, False)

    assert module.channel_writes == [(0, False)]
    assert module.parameter_writes == [
        (20145, True, 0),
        (20145, False, 0),
    ]


def test_last_channel_of_16_channel_dio_is_addressed_directly(monkeypatch) -> None:
    module = FakeModule("inout", channel_count=16)

    _call_set_output(monkeypatch, module, True, channel="X15")

    assert module.parameter_writes == [(20145, True, 15)]
    assert module.channel_writes == [(15, True)]


def test_direction_parameter_identifies_split_input_output_apdd(monkeypatch) -> None:
    module = FakeModule(
        "out",
        channel_count=16,
        has_direction_parameter=True,
    )

    _call_set_output(monkeypatch, module, True, channel="X0")

    assert module.parameter_writes == [(20145, True, 0)]
    assert module.channel_writes == [(0, True)]


def test_fixed_output_does_not_write_direction_parameter(monkeypatch) -> None:
    module = FakeModule("out")

    _call_set_output(monkeypatch, module, True)

    assert module.parameter_writes == []
    assert module.channel_writes == [(0, True)]


def test_set_output_returns_the_real_stage_error(monkeypatch) -> None:
    module = FakeModule("out")

    try:
        _call_set_output(monkeypatch, module, True, channel="X1")
    except api.HTTPException as exc:
        assert exc.status_code == 502
        assert "output channel mapping failed" in exc.detail
        assert "Port X1 maps outside" in exc.detail
    else:
        raise AssertionError("Expected the invalid output mapping to fail")
