import pytest
import os
import json
import time
from pathlib import Path
from hal import CpxApHardware, SafeSession
from config_models import BenchConfig, TestFilter, SafetyClass
from resolver import TestResolver
from repository import PocketBaseRepository, TestRunRecord, TestResultRecord

# Global state for pytest session
_session_run_id = ""
_session_results = {}
_pb_repo = None
_bench_config_inst = None

def pytest_addoption(parser):
    parser.addoption("--bench-config", action="store", default=None, help="Path to modern BenchConfig jsonc file")
    parser.addoption("--safety-class", action="store", default="safe", help="Filter tests by safety class (safe/caution/destructive)")
    parser.addoption("--test-filter", action="store", default=None, help="Filter tests by test_id pattern")

@pytest.fixture(scope="session")
def bench_config_path(request):
    val = request.config.getoption("--bench-config")
    if not val:
        val = os.environ.get("BENCH_CONFIG_PATH")
    return val

@pytest.fixture(scope="session")
def bench_config(bench_config_path):
    global _bench_config_inst
    if _bench_config_inst is not None:
        return _bench_config_inst

    # If path provided and exists, load it
    if bench_config_path and Path(bench_config_path).exists():
        with open(bench_config_path, "r", encoding="utf-8") as f:
            content = f.read()
        import re
        content_clean = re.sub(r'//.*?\n|/\*.*?\*/', '', content, flags=re.DOTALL)
        _bench_config_inst = BenchConfig.model_validate_json(content_clean)
        return _bench_config_inst

    # Otherwise, fall back to compiling from topology.jsonc + connections.jsonc
    topo_path = Path("topology.jsonc")
    conn_path = Path("connections.jsonc")
    if topo_path.exists() and conn_path.exists():
        with open(topo_path, encoding="utf-8") as f:
            topo_raw = json.load(f)
        with open(conn_path, encoding="utf-8") as f:
            conn_raw = json.load(f)
        _bench_config_inst = BenchConfig.from_legacy(
            topology_data=topo_raw,
            connections_data=conn_raw,
            bench_id=os.environ.get("TESTBENCH_ID", "default"),
            ip_address=os.environ.get("CPX_IP", ""),
        )
        return _bench_config_inst

    raise FileNotFoundError(
        "Could not load bench config. Please provide --bench-config or "
        "ensure topology.jsonc and connections.jsonc exist in the working directory."
    )

@pytest.fixture(scope="session")
def hw(bench_config):
    hw_iface = CpxApHardware()
    with SafeSession(hw_iface, bench_config.test_bench.ip_address) as session_hw:
        yield session_hw

@pytest.fixture(scope="session")
def resolved_plan(bench_config, request):
    filters = TestFilter()
    test_filter = request.config.getoption("--test-filter") or os.environ.get("TEST_FILTER")
    if test_filter:
        filters.test_id = test_filter
        
    safety_filter = request.config.getoption("--safety-class") or os.environ.get("SAFETY_CLASS_FILTER")
    if safety_filter:
        try:
            filters.safety_class = SafetyClass(safety_filter)
        except ValueError:
            pass

    resolver = TestResolver()
    return resolver.resolve(bench_config, filters)


def pytest_sessionstart(session):
    global _session_run_id, _pb_repo, _session_results
    _session_results = {
        "ip_address": "",
        "timestamp": time.time(),
        "tests": {}
    }
    
    # 1. Determine Run ID
    _session_run_id = os.environ.get("CI_JOB_ID") or f"run-{int(time.time())}"
    
    # 2. Check PocketBase env vars
    pb_url = os.environ.get("PB_URL") or os.environ.get("POCKETBASE_URL")
    pb_user = os.environ.get("PB_USERNAME")
    pb_pass = os.environ.get("PB_PASSWORD")
    
    if pb_url:
        _pb_repo = PocketBaseRepository(url=pb_url, username=pb_user, password=pb_pass)
        
        # Determine git commit details if running in Git
        commit_sha = os.environ.get("CI_COMMIT_SHA", "")
        if not commit_sha:
            try:
                import subprocess
                commit_sha = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode("utf-8").strip()
            except Exception:
                pass
                
        # Determine config commit details
        config_commit = os.environ.get("CONFIG_COMMIT", "")
        if not config_commit and Path("/tmp/config").exists():
            try:
                import subprocess
                config_commit = subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd="/tmp/config"
                ).decode("utf-8").strip()
            except Exception:
                pass

        # Create record in DB
        record = TestRunRecord(
            run_id=_session_run_id,
            test_bench_id=os.environ.get("TESTBENCH_ID", "default"),
            source="ci" if os.environ.get("CI_JOB_ID") else "cli",
            ip_address=os.environ.get("CPX_IP", ""),
            status="running",
            test_code_commit=commit_sha,
            config_commit=config_commit,
            gitlab_pipeline_id=os.environ.get("CI_PIPELINE_ID", ""),
            gitlab_job_id=os.environ.get("CI_JOB_ID", ""),
            resolved_plan_id="",
            schema_version="1.0",
            tests=[]
        )
        try:
            _pb_repo.create_test_run(record)
        except Exception as e:
            print(f"[conftest] Failed to log run start to PocketBase: {e}")


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    # execute all other hooks to obtain the report object
    outcome = yield
    report = outcome.get_result()
    
    # We only log results during the 'call' phase
    if report.when == "call":
        test_id = item.name
        # Simple parsing of unique test name (e.g. test_resolved_instance[connection-validation-mod-002])
        # Let's extract test_id and resolved instance info
        instance = None
        if hasattr(item, "callspec") and "resolved_instance" in item.callspec.params:
            instance = item.callspec.params["resolved_instance"]
            
        verdict = "passed" if report.passed else "failed"
        if report.failed and call.excinfo:
            verdict = "failed"
            error_msg = str(call.excinfo.value)
            stack_trace = str(report.longrepr)
            exc_type = call.excinfo.typename
        else:
            error_msg = ""
            stack_trace = ""
            exc_type = ""
            
        # Log checkpoint to PocketBase
        if _pb_repo and _session_run_id:
            try:
                # Add test result record
                res_rec = TestResultRecord(
                    run_id=_session_run_id,
                    test_id=instance.test_id if instance else test_id,
                    test_version=instance.test_version if instance else "1.0.0",
                    test_name=instance.test_name if instance else test_id,
                    module_instance_id=instance.module_instance_id if instance else "",
                    module_code=instance.module_code if instance else 0,
                    product_key=instance.product_key if instance else "",
                    channel_id=instance.channel_id if instance else None,
                    verdict=verdict,
                    start_time=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(call.start)),
                    end_time=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(call.stop)),
                    duration_ms=round((call.stop - call.start) * 1000, 1),
                    failure_reason=error_msg,
                    exception_type=exc_type,
                    stack_trace=stack_trace
                )
                _pb_repo.add_test_result(res_rec)
            except Exception as e:
                print(f"[conftest] Failed to log checkpoint to PocketBase: {e}")
                
        # Record locally for results.json
        test_key = instance.test_id if instance else test_id
        if test_key not in _session_results["tests"]:
            _session_results["tests"][test_key] = {
                "test_id": test_key,
                "passed": True,
                "results": [],
                "duration_ms": 0.0
            }
            
        result_entry = {
            "passed": report.passed,
            "duration_ms": round((call.stop - call.start) * 1000, 1),
        }
        if instance:
            result_entry["module"] = str(instance.module_address)
            result_entry["module_name"] = instance.test_name
            result_entry["address"] = instance.module_address
            if instance.channel_id:
                result_entry["channel"] = instance.channel_id
            if instance.wiring_id:
                result_entry["wiring_id"] = instance.wiring_id
                
        if not report.passed:
            _session_results["tests"][test_key]["passed"] = False
            result_entry["error"] = error_msg
            result_entry["traceback"] = stack_trace

        _session_results["tests"][test_key]["results"].append(result_entry)
        _session_results["tests"][test_key]["duration_ms"] += result_entry["duration_ms"]


def pytest_sessionfinish(session, exitstatus):
    global _session_run_id, _pb_repo, _session_results, _bench_config_inst
    
    # Update IP address from bench config
    if _bench_config_inst:
        _session_results["ip_address"] = _bench_config_inst.test_bench.ip_address
        
    # Write combined results.json for CI / web reporting
    with open("results.json", "w", encoding="utf-8") as f:
        json.dump(_session_results, f, indent=2, default=str)
        
    # Update PocketBase run status
    if _pb_repo and _session_run_id:
        status = "completed" if exitstatus == 0 else "failed"
        try:
            _pb_repo.update_test_run(_session_run_id, status)
        except Exception as e:
            print(f"[conftest] Failed to update run status in PocketBase: {e}")
