"""Quick import verification script."""
import sys
sys.path.insert(0, ".")

errors = []

# Core modules
try:
    from tests._base import make_param, LogFn, noop_log, channel_index_from_port
    print("✓ tests._base")
except Exception as e:
    errors.append(f"tests._base: {e}")

try:
    from hal import HardwareInterface, CpxApHardware, SafeSession, ModuleInfo
    print("✓ hal")
except Exception as e:
    errors.append(f"hal: {e}")

try:
    from repository import PocketBaseRepository, TestRunRecord, TestResultRecord
    print("✓ repository")
except Exception as e:
    errors.append(f"repository: {e}")

try:
    from test_runner import run_all_tests, psu_power_cycle
    print("✓ test_runner")
except Exception as e:
    errors.append(f"test_runner: {e}")

try:
    from tests.validate_connections import run, validate_single
    print("✓ tests.validate_connections")
except Exception as e:
    errors.append(f"tests.validate_connections: {e}")

try:
    from tests.condition_counter import run
    print("✓ tests.condition_counter")
except Exception as e:
    errors.append(f"tests.condition_counter: {e}")

try:
    from tests.valve_condition_counter import run
    print("✓ tests.valve_condition_counter")
except Exception as e:
    errors.append(f"tests.valve_condition_counter: {e}")

try:
    from tests.remanent_params import run, verify
    print("✓ tests.remanent_params")
except Exception as e:
    errors.append(f"tests.remanent_params: {e}")

try:
    from tests.compare_topology import run
    print("✓ tests.compare_topology")
except Exception as e:
    errors.append(f"tests.compare_topology: {e}")

if errors:
    print(f"\n✗ {len(errors)} ERROR(S):")
    for e in errors:
        print(f"  {e}")
    sys.exit(1)
else:
    print("\n✓ ALL IMPORTS PASSED")
