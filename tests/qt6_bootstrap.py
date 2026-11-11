"""Run one test process against the verified embedded Qt 6 runtime."""
import os
import runpy
import sys
from pathlib import Path

APP = Path.cwd()
sys.path.insert(0, str(APP))
runtime = os.environ.get("VWA_TEST_QT6_ROOT")
if not runtime:
    raise SystemExit("VWA_TEST_QT6_ROOT must point at the verified Qt 6 runtime")
from quick_runtime import configure_quick_runtime
configure_quick_runtime(APP, runtime, software=True)
test_file = Path(sys.argv[1]).resolve()
if APP not in test_file.parents:
    raise SystemExit(f"Test file escaped the isolated application: {test_file}")
runpy.run_path(str(test_file), run_name="__main__")
