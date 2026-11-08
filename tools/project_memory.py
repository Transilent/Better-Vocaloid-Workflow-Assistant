"""Run the canonical project memory implementation without an app runtime."""
from pathlib import Path
import sys
import sqlite3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from vwa_project_memory import main

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, sqlite3.Error) as error:
        print('MEMORY ERROR: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
