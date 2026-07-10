import contextlib

import pytest

from config_models import BenchConfig
from hal import HardwareInterface
from resolver import ResolvedTestInstance


def pytest_generate_tests(metafunc):
    if "resolved_instance" in metafunc.fixturenames:
        import json
        import os
        from pathlib import Path

        from config_models import BenchConfig, SafetyClass
        from resolver import TestFilter, TestResolver

        # Get config path option
        config_path = metafunc.config.getoption("--bench-config") or os.environ.get("BENCH_CONFIG_PATH")
        
        # Load config
        bench_config_inst = None
        if config_path and Path(config_path).exists():
            with open(config_path, encoding="utf-8") as f:
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
                with contextlib.suppress(ValueError):
                    filters.safety_class = SafetyClass(safety_filter)
            
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
def test_resolved_instance(hw: HardwareInterface, resolved_instance: ResolvedTestInstance, bench_config: BenchConfig, bench_config_path: str):
    test_id = resolved_instance.test_id
    
    def log(level: str, msg: str):
        print(f"[{level.upper()}] {msg}")
        
    if test_id == "connection-validation":
        from tests.test_validate_connections import run as run_validate
        res = run_validate(
            hw_or_ip=hw,
            log=log,
            bench_config=bench_config,
            module_address=resolved_instance.module_address
        )
        assert res.get("all_passed"), f"Wiring validation failed: {res.get('error') or res.get('results')}"

    elif test_id == "remanent-params":
        from tests.test_remanent_params import run_with_power_cycle as run_rem_pc
        res = run_rem_pc(
            hw=hw,
            log=log,
            bench_config=bench_config,
            module_address=resolved_instance.module_address,
        )
        failed = [r for r in res if r.get("passed") is False]
        assert not failed, f"Remanent parameters test failed: {failed}"

    elif test_id == "factory-reset":
        from tests.test_factory_reset import run as run_fr
        res = run_fr(
            hw=hw,
            log=log,
            bench_config=bench_config,
            module_address=resolved_instance.module_address,
        )
        failed = [r for r in res if r.get("passed") is False]
        assert not failed, f"Factory reset test failed: {failed}"

    elif test_id == "open-load-diag":
        from tests.test_open_load_diag import run as run_old
        res = run_old(
            hw=hw,
            log=log,
            bench_config=bench_config,
            module_address=resolved_instance.module_address,
        )
        failed = [r for r in res if r.get("passed") is False]
        assert not failed, f"Open-load diagnostic test failed: {failed}"

    elif test_id == "condition-counter":
        from tests.test_condition_counter import run_with_power_cycle as run_cc_pc
        res = run_cc_pc(
            hw=hw,
            log=log,
            bench_config=bench_config,
            module_address=resolved_instance.module_address,
        )
        failed = [r for r in res if r.get("passed") is False]
        assert not failed, f"Condition counter check failed for connections: {failed}"

    elif test_id == "valve-condition-counter":
        from tests.test_valve_condition_counter import run as run_vcc
        res = run_vcc(
            hw=hw,
            log=log,
            bench_config=bench_config,
            module_address=resolved_instance.module_address
        )
        failed = [r for r in res if r.get("passed") is False]
        assert not failed, f"Valve condition counter failed: {failed}"

    elif test_id == "output-toggle":
        from tests.test_output_toggle import run as run_output_toggle
        res = run_output_toggle(
            hw=hw,
            log=log,
            bench_config=bench_config,
            module_address=resolved_instance.module_address
        )
        failed = [r for r in res if r.get("passed") is False]
        assert not failed, f"Output toggle failed: {failed}"

    elif test_id == "valve-toggle":
        from tests.test_valve_toggle import run as run_valve_toggle
        res = run_valve_toggle(
            hw=hw,
            log=log,
            bench_config=bench_config,
            module_address=resolved_instance.module_address
        )
        failed = [r for r in res if r.get("passed") is False]
        assert not failed, f"Valve toggle failed: {failed}"

    elif test_id == "dio-toggle":
        from tests.test_dio_toggle import run as run_dio_toggle
        res = run_dio_toggle(
            hw=hw,
            log=log,
            bench_config=bench_config,
            module_address=resolved_instance.module_address
        )
        failed = [r for r in res if r.get("passed") is False]
        assert not failed, f"DIO toggle failed: {failed}"

    elif test_id == "compare-topology":
        from tests.test_compare_topology import run as run_compare
        res = run_compare(
            hw=hw,
            log=log,
            bench_config=bench_config,
            module_address=resolved_instance.module_address
        )
        assert res.get("passed") or res.get("all_passed"), f"Topology Comparison failed: {res}"

    elif test_id == "system-diagnosis":
        from tests.test_system_diagnosis import run as run_sysdiag
        res = run_sysdiag(
            hw=hw, 
            log=log,
            bench_config=bench_config,
            module_address=resolved_instance.module_address
        )
        log("info", f"Global System Diagnosis: {res.get('diagnosis')}")
        assert res.get("passed"), "Failed to read system diagnosis"

    elif test_id == "test-api":
        from tests.test_api import run as run_test_api
        res = run_test_api(
            hw=hw,
            log=log,
            bench_config=bench_config,
            module_address=resolved_instance.module_address,
        )
        failed = [r for r in res.get("results", []) if r.get("passed") is False]
        assert not failed, f"Test API failed: {failed}"

    else:
        pytest.skip(f"Test type '{test_id}' has no runner defined in test_suite.py")
