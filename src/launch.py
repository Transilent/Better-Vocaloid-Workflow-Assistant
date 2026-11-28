"""Start the Qt Quick desktop; write native startup errors for the launcher."""
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True


def _maybe_run_full_migrator():
    """If a 2-hop full-package migration handoff is pending, open the migrator first."""
    import json

    handoff = ROOT / 'cache/updates/full-migrator-handoff.json'
    if not handoff.is_file():
        return None
    try:
        data = json.loads(handoff.read_text(encoding='utf-8-sig'))
        target_version = str(data.get('target_version') or '').strip()
        source = str(data.get('source') or 'auto').strip()
        if not target_version:
            return None
        import full_package_migrator
        rc = full_package_migrator.run_gui(ROOT, target_version, source)
        return None if rc == 2 else rc
    except Exception:
        return None


def main(argv=None):
    migrator_rc = _maybe_run_full_migrator()
    if migrator_rc is not None:
        return migrator_rc
    from quick_app import main as start
    args = sys.argv[1:] if argv is None else argv
    # Existing shortcut arguments still resolve to the single desktop entry.
    return start([value for value in args if value != '--quick'])


if __name__ == '__main__':
    log = None
    if '--desktop' in sys.argv:
        import faulthandler
        try:
            path = ROOT / 'launcher.log'
            if path.is_file() and path.stat().st_size >= 2 * 1024**2:
                path.replace(ROOT / 'launcher.log.1')
            log = path.open('a', encoding='utf-8', buffering=1)
            sys.stdout = sys.stderr = log
            faulthandler.enable(log)
        except OSError:
            pass
        try:
            from crash_dump import enable_crash_capture
            enable_crash_capture(ROOT)
        except OSError:
            pass
    try:
        result = main()
    except Exception as error:
        from operation_diagnostics import journal_for
        journal_for(ROOT).problem('startup', error)
        traceback.print_exc()
        result = 1
    finally:
        from operation_diagnostics import close_journal
        close_journal(ROOT)
    sys.exit(result)
