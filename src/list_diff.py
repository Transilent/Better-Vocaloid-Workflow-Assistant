"""Pure-Python minimal list diff engine for incremental QAbstractListModel updates.

Zero Qt imports so it stays 100% unit-testable in L0 pure-Python runs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence


@dataclass(frozen=True)
class DiffOp:
    kind: str  # 'remove' | 'insert' | 'update' | 'reset'
    first: int
    last: int  # inclusive for remove/insert/update


def compute_list_diff(
    old_rows: Sequence[Any],
    new_rows: Sequence[Any],
    key_fn: Callable[[Any], Any],
) -> list[DiffOp]:
    """Compute a minimal sequence of incremental list mutations from *old_rows* to *new_rows*.

    When order is preserved for surviving items (the common case for task lists,
    track lists, and phrase lists), emits targeted remove/insert/update ops
    instead of a full model reset. Falls back to a single ``reset`` op when items
    are reordered.
    """
    old_keys = [key_fn(r) for r in old_rows]
    new_keys = [key_fn(r) for r in new_rows]

    if old_keys == new_keys:
        ops: list[DiffOp] = []
        run_start: int | None = None
        for idx, (o, n) in enumerate(zip(old_rows, new_rows)):
            if o != n:
                if run_start is None:
                    run_start = idx
            elif run_start is not None:
                ops.append(DiffOp("update", run_start, idx - 1))
                run_start = None
        if run_start is not None:
            ops.append(DiffOp("update", run_start, len(new_rows) - 1))
        return ops

    # Duplicate keys cannot be diffed unambiguously; reset cleanly.
    if len(set(old_keys)) != len(old_keys) or len(set(new_keys)) != len(new_keys):
        return [DiffOp("reset", 0, max(0, len(new_rows) - 1))]

    # Check if surviving keys preserve relative order (no reordering).
    new_pos = {k: i for i, k in enumerate(new_keys)}
    common_old_indices = [i for i, k in enumerate(old_keys) if k in new_pos]
    common_new_indices = [new_pos[old_keys[i]] for i in common_old_indices]
    if common_new_indices != sorted(common_new_indices):
        return [DiffOp("reset", 0, max(0, len(new_rows) - 1))]

    ops = []
    # 1. Remove contiguous runs of vanished keys from back to front so indices stay valid.
    keep_set = set(new_keys)
    working_rows = list(old_rows)
    working_keys = list(old_keys)
    idx = len(working_keys) - 1
    while idx >= 0:
        if working_keys[idx] not in keep_set:
            end = idx
            while idx > 0 and working_keys[idx - 1] not in keep_set:
                idx -= 1
            ops.append(DiffOp("remove", idx, end))
            del working_rows[idx : end + 1]
            del working_keys[idx : end + 1]
        idx -= 1

    # 2. Insert new keys in contiguous runs from front to back.
    w_idx = 0
    n_idx = 0
    while n_idx < len(new_keys):
        if w_idx < len(working_keys) and working_keys[w_idx] == new_keys[n_idx]:
            w_idx += 1
            n_idx += 1
            continue
        start = n_idx
        while n_idx < len(new_keys) and (w_idx >= len(working_keys) or working_keys[w_idx] != new_keys[n_idx]):
            n_idx += 1
        end = n_idx - 1
        ops.append(DiffOp("insert", start, end))
        for k in range(start, end + 1):
            working_keys.insert(k, new_keys[k])
            working_rows.insert(k, new_rows[k])
        w_idx += end - start + 1

    # 3. Emit update ops for surviving rows whose payload changed.
    old_by_key = {key_fn(r): r for r in old_rows}
    run_start = None
    for idx, row in enumerate(new_rows):
        key = new_keys[idx]
        if key in old_by_key and old_by_key[key] != row:
            if run_start is None:
                run_start = idx
        elif run_start is not None:
            ops.append(DiffOp("update", run_start, idx - 1))
            run_start = None
    if run_start is not None:
        ops.append(DiffOp("update", run_start, len(new_rows) - 1))

    return ops
