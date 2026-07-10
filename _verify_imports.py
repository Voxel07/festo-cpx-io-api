"""Quick import verification script."""
import sys

sys.path.insert(0, ".")

errors = []

# Core modules
try:
    print("✓ tests._base")
except Exception as e:
    errors.append(f"tests._base: {e}")

try:
    print("✓ hal")
except Exception as e:
    errors.append(f"hal: {e}")

try:
    print("✓ repository")
except Exception as e:
    errors.append(f"repository: {e}")

try:
    print("✓ test_runner")
except Exception as e:
    errors.append(f"test_runner: {e}")

try:
    print("✓ tests.validate_connections")
except Exception as e:
    errors.append(f"tests.validate_connections: {e}")

try:
    print("✓ tests.condition_counter")
except Exception as e:
    errors.append(f"tests.condition_counter: {e}")

try:
    print("✓ tests.valve_condition_counter")
except Exception as e:
    errors.append(f"tests.valve_condition_counter: {e}")

try:
    print("✓ tests.remanent_params")
except Exception as e:
    errors.append(f"tests.remanent_params: {e}")

try:
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
