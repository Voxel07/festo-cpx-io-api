import pytest
from resolver import ResolvedTestInstance
from hal import HardwareInterface
from config_models import BenchConfig

def pytest_generate_tests(metafunc):
    if "resolved_instance" in metafunc.fixturenames:
        import os
        import json
        from pathlib import Path
        from config_models import BenchConfig, TestFilter, SafetyClass
        from resolver import TestResolver

        # Get config path option
        config_path = metafunc.config.getoption("--bench-config") or os.environ.get("BENCH_CONFIG_PATH")
        
        # Load config
        bench_config_inst = None
        if config_path and Path(config_path).exists():
            with open(config_path, "r", encoding="utf-8") as f:
                content = f.read()
            import re
            content_clean = re.sub(r'//.*?\n|/\*.*?\*/', '', content, flags=re.DOTALL)
            bench_config_inst = BenchConfig.model_validate_json(content_clean)
        else:
            topo_path = Path("topology.jsonc")
            conn_path = Path("connections.jsonc")
            if topo_path.exists() and conn_path.exists():
                with open(topo_path, encoding="utf-8") as f:
                    topo_raw = json.load(f)
                with open(conn_path, encoding="utf-8") as f:
                    conn_raw = json.load(f)
                bench_config_inst = BenchConfig.from_legacy(
                    topology_data=topo_raw,
                    connections_data=conn_raw,
                    bench_id=os.environ.get("TESTBENCH_ID", "default"),
                    ip_address=os.environ.get("CPX_IP", ""),
                )
                
        if bench_config_inst:
            filters = TestFilter()
            test_filter = metafunc.config.getoption("--test-filter") or os.environ.get("TEST_FILTER")
            if test_filter:
                filters.test_id = test_filter
                
            safety_filter = metafunc.config.getoption("--safety-class") or os.environ.get("SAFETY_CLASS_FILTER")
            if safety_filter:
                try:
                    filters.safety_class = SafetyClass(safety_filter)
                except ValueError:
                    pass
            
            resolver = TestResolver()
            plan = resolver.resolve(bench_config_inst, filters)
            
            # Parametrise metafunc
            metafunc.parametrize(
                "resolved_instance",
                plan.instances,
                ids=[inst.unique_id for inst in plan.instances]
            )
        else:
            metafunc.parametrize("resolved_instance", [])


@pytest.mark.hardware
def test_resolved_instance(hw: HardwareInterface, resolved_instance: ResolvedTestInstance, bench_config: BenchConfig):
    test_id = resolved_instance.test_id
    addr = resolved_instance.module_address
    
    def log(level: str, msg: str):
        print(f"[{level.upper()}] {msg}")
        
    if test_id == "connection-validation":
        from tests.validate_connections import run as run_validate
        wire = next((w for w in bench_config.wiring if w.id == resolved_instance.wiring_id), None)
        if not wire:
            pytest.fail(f"Wiring connection '{resolved_instance.wiring_id}' not found in configuration")
            
        src_mod = next((m for m in bench_config.module_instances if m.instance_id == wire.source_instance_id), None)
        tgt_mod = next((m for m in bench_config.module_instances if m.instance_id == wire.target_instance_id), None)
        if not src_mod or not tgt_mod:
            pytest.fail(f"Modules for wiring '{wire.id}' not found in configuration")
            
        conn = {
            "source_module_addr": src_mod.address,
            "source_channel": wire.source_channel,
            "target_module_addr": tgt_mod.address,
            "target_channel": wire.target_channel,
        }
        res = run_validate(
            hw_or_ip=hw,
            log=log,
            connections=[conn],
            pulse_duration_s=resolved_instance.parameters.get("pulse_duration_s", 0.3)
        )
        assert res.get("all_passed"), f"Wiring validation failed: {res.get('error') or res.get('results')}"

    elif test_id == "condition-counter":
        from tests.condition_counter import run as run_cc
        conns = []
        for wire in bench_config.wiring:
            if wire.target_instance_id == resolved_instance.module_instance_id:
                src_mod = next((m for m in bench_config.module_instances if m.instance_id == wire.source_instance_id), None)
                tgt_mod = next((m for m in bench_config.module_instances if m.instance_id == wire.target_instance_id), None)
                if src_mod and tgt_mod:
                    conns.append({
                        "source_module_addr": src_mod.address,
                        "source_channel": wire.source_channel,
                        "target_module_addr": tgt_mod.address,
                        "target_channel": wire.target_channel,
                    })
        if not conns:
            pytest.skip(f"No wiring connections target module {resolved_instance.module_instance_id} for condition counter test")
            
        res = run_cc(
            hw=hw,
            log=log,
            cc_param_id=resolved_instance.parameters.get("cc_param_id", 20094),
            cc_readback_param_id=resolved_instance.parameters.get("cc_readback_param_id", 20095),
            toggle_cycles=resolved_instance.parameters.get("toggle_cycles", 3),
            connections=conns
        )
        failed = [r for r in res if r.get("passed") is False]
        assert not failed, f"Condition counter check failed for connections: {failed}"

    elif test_id == "remanent-params":
        from tests.remanent_params import run as run_rem
        res = run_rem(
            hw=hw,
            log=log,
            param_id_1=resolved_instance.parameters.get("param_id_1", 20118),
            param_id_2=resolved_instance.parameters.get("param_id_2", 20119),
            module_address=resolved_instance.module_address
        )
        failed = [r for r in res if r.get("passed") is False]
        assert not failed, f"Remanent parameters test failed: {failed}"

    elif test_id == "valve-condition-counter":
        from tests.valve_condition_counter import run as run_vcc
        res = run_vcc(
            hw=hw,
            log=log,
            toggle_cycles=resolved_instance.parameters.get("toggle_cycles", 5),
            cc_param_id=resolved_instance.parameters.get("cc_param_id", 20094),
            cc_readback_param_id=resolved_instance.parameters.get("cc_readback_param_id", 20095),
            module_address=resolved_instance.module_address
        )
        failed = [r for r in res if r.get("passed") is False]
        assert not failed, f"Valve condition counter failed: {failed}"

    elif test_id in ("valve-toggle", "output-toggle"):
        from tests.output_toggle import run as run_output_toggle
        res = run_output_toggle(
            hw=hw,
            log=log,
            pulse_duration_s=resolved_instance.parameters.get("pulse_duration_s", 0.2),
            module_address=resolved_instance.module_address
        )
        failed = [r for r in res if r.get("passed") is False]
        assert not failed, f"Output toggle failed: {failed}"

    elif test_id == "compare-topology":
        from tests.compare_topology import run as run_compare
        # Compare topology for the entire bus
        res = run_compare(
            stored_path="topology.jsonc",
            hw=hw,
            log=log
        )
        assert res.get("passed") or res.get("all_passed"), f"Topology Comparison failed: {res}"

    elif test_id == "system-diagnosis":
        # System diagnosis
        diag = hw.read_diagnosis(addr)
        log("info", f"Global System Diagnosis: {diag}")
        assert diag is not None, "Failed to read system diagnosis"

    else:
        pytest.skip(f"Test type '{test_id}' has no runner defined in test_suite.py")
