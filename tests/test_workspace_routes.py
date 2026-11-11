"""Stable page routes remain usable before any Qt toolkit is selected."""
from pathlib import Path
import sys

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))

import workspace_routes

assert not any(name == 'PyQt5' or name.startswith('PyQt5.')
               or name == 'PySide6' or name.startswith('PySide6.')
               for name in sys.modules), 'The route registry imported a Qt toolkit'

expected = {
    'processing': (0, None, None),
    'components': (1, None, None),
    'results': (2, None, None),
    'publishing': (3, None, None),
    'updates': (4, None, None),
    'workflow': (5, 'processing', None),
    'storage': (6, 'updates', None),
    'subtitles': (7, 'processing', 'subtitles'),
    'video_mixdown': (8, None, None),
    'tuning': (9, None, None),
    'audio_mixdown': (10, None, None),
}
actual = {
    spec.page_id: (spec.legacy_index, spec.target, spec.focus_step)
    for spec in workspace_routes.PAGE_SPECS
}
assert actual == expected
assert [spec.legacy_index for spec in workspace_routes.PAGE_SPECS] == list(range(11))
orders = [spec.sidebar_order for spec in workspace_routes.PAGE_SPECS
          if spec.sidebar_order is not None]
assert len(orders) == len(set(orders))
for spec in workspace_routes.PAGE_SPECS:
    if spec.target:
        destination = next(item for item in workspace_routes.PAGE_SPECS
                           if item.page_id == spec.target)
        assert destination.target is None
print('PASS: route registry is Qt-free and stable IDs, indices and aliases are unchanged')