"""Generic incremental QAbstractListModel backed by list_diff.compute_list_diff.

Eliminates full-list QVariantList teardown/recreation flicker in QML ListViews
when rows are updated, appended, or removed.
"""
from __future__ import annotations

from typing import Any, Callable, Sequence

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, Property, Signal, Slot

from list_diff import compute_list_diff

ROW_DATA_ROLE = Qt.UserRole + 1


class VwaListModel(QAbstractListModel):
    """Reusable QML-friendly incremental list model over dicts or structured rows."""

    countChanged = Signal()

    def __init__(self, key_fn: Callable[[Any], Any], parent=None):
        super().__init__(parent)
        self._key_fn = key_fn
        self._rows: list[Any] = []

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = index.row()
        if row < 0 or row >= len(self._rows):
            return None
        if role in (ROW_DATA_ROLE, Qt.DisplayRole):
            return self._rows[row]
        return None

    def roleNames(self) -> dict[int, bytes]:
        return {ROW_DATA_ROLE: b"rowData"}

    @Property(int, notify=countChanged)
    def count(self) -> int:
        return len(self._rows)

    @Slot(int, result="QVariant")
    def rowAt(self, index: int) -> Any:
        if 0 <= index < len(self._rows):
            return self._rows[index]
        return None

    def sync_rows(self, new_rows: Sequence[Any]) -> list[str]:
        """Apply minimal incremental model mutations to match *new_rows*.

        Returns the list of diff op kinds applied (useful for tests/telemetry).
        """
        incoming = list(new_rows)
        old_count = len(self._rows)
        ops = compute_list_diff(self._rows, incoming, self._key_fn)
        if not ops:
            return []

        applied: list[str] = []
        if len(ops) == 1 and ops[0].kind == "reset":
            self.beginResetModel()
            self._rows = incoming
            self.endResetModel()
            applied.append("reset")
        else:
            for op in ops:
                if op.kind == "remove":
                    self.beginRemoveRows(QModelIndex(), op.first, op.last)
                    del self._rows[op.first : op.last + 1]
                    self.endRemoveRows()
                    applied.append("remove")
                elif op.kind == "insert":
                    self.beginInsertRows(QModelIndex(), op.first, op.last)
                    for idx in range(op.first, op.last + 1):
                        self._rows.insert(idx, incoming[idx])
                    self.endInsertRows()
                    applied.append("insert")
                elif op.kind == "update":
                    for idx in range(op.first, op.last + 1):
                        self._rows[idx] = incoming[idx]
                    top = self.index(op.first, 0)
                    bottom = self.index(op.last, 0)
                    self.dataChanged.emit(top, bottom, [ROW_DATA_ROLE, Qt.DisplayRole])
                    applied.append("update")
        if len(self._rows) != old_count:
            self.countChanged.emit()
        return applied
