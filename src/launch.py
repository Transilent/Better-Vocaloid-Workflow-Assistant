"""Start the Qt Quick desktop; write native startup errors for the launcher."""
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True


def main(argv=None):
    from quick_app import main as start
    args = sys.argv[1:] if argv is None else argv
    # Existing shortcut arguments still resolve to the single desktop entry.
    return start([value for value in args if value != '--quick'])


if __name__ == '__main__':
    if '--desktop' in sys.argv:
        import faulthandler
        log = (ROOT / 'launcher.log').open('a', encoding='utf-8', buffering=1)
        sys.stdout = sys.stderr = log
        faulthandler.enable(log)
    try:
        result = main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
    sys.exit(result)
