"""Small bootstrap; failures, including native Qt errors, go to launcher.log."""
import json
import os
import runpy
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True


def main():
    from common import load_config
    from check_dependencies import validate
    validate()
    config = load_config()
    if "--check" in sys.argv:
        print("Better Vocaloid Workflow Assistant dependencies ready", flush=True)
        print("Runtime:", config["python"], flush=True)
        return
    runtime = Path(config["python"]).parent
    qt = runtime / "Lib/site-packages/PyQt5/Qt5"
    os.environ["QT_PLUGIN_PATH"] = str(qt / "plugins")
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(qt / "plugins/platforms")
    os.environ["PATH"] = os.pathsep.join([
        str(runtime), str(runtime / "DLLs"), str(qt / "bin"),
        os.environ.get("PATH", ""),
    ])
    print("Better Vocaloid Workflow Assistant startup", sys.version, flush=True)
    print("Runtime:", runtime, flush=True)
    if "--smoke-test" in sys.argv:
        namespace = runpy.run_path(str(ROOT / "app.py"))
        from PyQt5.QtCore import QTimer
        application = namespace["QApplication"]([])
        window = namespace["Window"]()
        window.show()
        application.processEvents()
        if not window.isVisible():
            raise RuntimeError("Application window did not initialize")
        QTimer.singleShot(1000, application.quit)
        application.exec_()
        print("GUI ready; startup smoke test passed", flush=True)
        return
    runpy.run_path(str(ROOT / "app.py"), run_name="__main__")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
