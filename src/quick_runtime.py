"""Process-local Qt 6 runtime selection and the shared desktop instance lock."""

from __future__ import annotations

import os
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Any


QT6_VERSION = "6.10.3"
DEFAULT_RUNTIME = Path("runtime/qt6")
_QT5_PREFIX = 'dependencies/vocal2midi/python/lib/site-packages/'
_QT5_PACKAGES = ('pyqt5', 'pyqt_fluent_widgets-', 'qfluentwidgets', 'qframelesswindow', 'darkdetect')


def legacy_widget_runtime_file(name: str) -> bool:
    """The frozen inference manifest may still describe a published Qt 5 payload."""
    name = name.casefold()
    return name.startswith(_QT5_PREFIX) and name[len(_QT5_PREFIX):].split('/', 1)[0].startswith(_QT5_PACKAGES)


def installed_for_program_update(root) -> bool:
    """Only a canonical, installed Quick launcher can restart into an update."""
    root = Path(root)
    try:
        version = json.loads((root / 'version.json').read_text(encoding='utf-8-sig'))
        return (version.get('desktop_frontend') == 'quick' and version.get('qt_version') == QT6_VERSION
                and (root / 'VWA.exe').is_file()
                and (root / DEFAULT_RUNTIME / 'PySide6/plugins/platforms/qwindows.dll').is_file())
    except (OSError, ValueError, AttributeError):
        return False

# These modules form the Quick host baseline. Keep the list limited to imports
# used by the application host so a trimmed runtime still fails for a concrete
# missing page dependency rather than an unrelated Qt feature.
_QML_MODULES = (
    "QtQml",
    "QtQuick",
    "QtQuick.Window",
    "QtQuick.Layouts",
    "QtQuick.Templates",
    "QtQuick.Controls",
    "QtQuick.Controls.Basic",
    "QtQuick.Dialogs",
    "QtQuick.Effects",
    "QtQuick.Shapes",
)
_MIXED_QT_MODULE_PREFIXES = ("PyQt5", "PyQt6", "PySide2")
_DLL_DIRECTORY_HANDLES: dict[Path, Any] = {}


def _module_file(module: ModuleType) -> Path | None:
    filename = getattr(module, "__file__", None)
    return Path(filename).resolve() if filename else None


def _check_loaded_module(name: str, expected: Path) -> None:
    module = sys.modules.get(name)
    if module is None:
        return
    actual = _module_file(module)
    if actual is None or not actual.is_relative_to(expected):
        raise RuntimeError(
            f"{name} was already imported from {actual or 'an unknown location'}; "
            f"refusing to mix Qt runtimes with {expected}"
        )


def _check_no_mixed_qt() -> None:
    mixed = sorted(
        name for name in sys.modules
        if any(name == prefix or name.startswith(prefix + ".") for prefix in _MIXED_QT_MODULE_PREFIXES)
    )
    if mixed:
        raise RuntimeError(
            "Quick startup found another Qt binding already loaded in this process: "
            + ", ".join(mixed[:6])
        )


def _require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise RuntimeError(f"Quick runtime is missing {label}: {path}")


def _check_version(module: ModuleType, name: str) -> None:
    version = getattr(module, "__version__", None)
    if version != QT6_VERSION:
        raise RuntimeError(
            f"Quick runtime requires {name} {QT6_VERSION}, found {version or 'an unknown version'}"
        )


def _prepend_path(value: str) -> None:
    entries = [entry for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
    normalized = os.path.normcase(os.path.abspath(value))
    entries = [entry for entry in entries if os.path.normcase(os.path.abspath(entry)) != normalized]
    os.environ["PATH"] = os.pathsep.join([value, *entries])


def configure_quick_runtime(root, override=None, *, software: bool = False) -> Path:
    """Select one process-local PySide6 runtime and return its resolved root.

    ``root`` is the application root. The default location is
    ``<root>/runtime/qt6``; an override must be an explicit absolute path. All
    path variables are replaced instead of appended so an inherited PyQt5 or
    different Qt 6 installation cannot supply plugins or QML modules.
    """

    if override is None:
        runtime = (Path(root).resolve() / DEFAULT_RUNTIME).resolve()
    else:
        requested = Path(override).expanduser()
        if not requested.is_absolute():
            raise ValueError(f"Quick runtime override must be an absolute path: {override}")
        runtime = requested.resolve()

    pyside = runtime / "PySide6"
    shiboken = runtime / "shiboken6"
    plugins = pyside / "plugins"
    qml = pyside / "qml"
    if not runtime.is_dir():
        raise RuntimeError(f"Quick runtime does not exist: {runtime}")
    if not pyside.is_dir() or not shiboken.is_dir():
        raise RuntimeError(f"Quick runtime must contain PySide6 and shiboken6: {runtime}")
    _require_file(plugins / "platforms/qwindows.dll", "the Windows platform plugin")
    _require_file(qml / "QtQml/qmldir", "the QtQml QML module")
    for uri in _QML_MODULES:
        _require_file(qml / Path(*uri.split(".")) / "qmldir", f"the {uri} QML module")

    _check_no_mixed_qt()
    _check_loaded_module("PySide6", pyside)
    _check_loaded_module("shiboken6", shiboken)

    runtime_entry = str(runtime)
    while runtime_entry in sys.path:
        sys.path.remove(runtime_entry)
    sys.path.insert(0, runtime_entry)

    if os.name == "nt" and hasattr(os, "add_dll_directory"):
        for folder in (pyside, shiboken):
            if folder not in _DLL_DIRECTORY_HANDLES:
                _DLL_DIRECTORY_HANDLES[folder] = os.add_dll_directory(str(folder))
    dll_folders = (runtime, pyside, shiboken)
    if (pyside / "bin").is_dir():
        dll_folders += (pyside / "bin",)
    for folder in reversed(dll_folders):
        _prepend_path(str(folder))

    os.environ["QT_PLUGIN_PATH"] = str(plugins)
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(plugins / "platforms")
    os.environ["QML2_IMPORT_PATH"] = str(qml)
    os.environ["QML_IMPORT_PATH"] = str(qml)
    if software:
        os.environ["QT_QUICK_BACKEND"] = "software"
    else:
        os.environ.pop("QT_QUICK_BACKEND", None)

    import PySide6
    import shiboken6
    from PySide6 import QtQml, QtQuick, QtQuickControls2  # noqa: F401
    from PySide6.QtCore import qVersion
    from PySide6.QtGui import QGuiApplication  # noqa: F401
    from PySide6.QtQml import QQmlApplicationEngine  # noqa: F401
    from PySide6.QtQuickControls2 import QQuickStyle  # noqa: F401

    _check_version(PySide6, "PySide6")
    _check_version(shiboken6, "shiboken6")
    if qVersion() != QT6_VERSION:
        raise RuntimeError(
            f"Quick runtime requires Qt {QT6_VERSION}, its native library reports {qVersion()}"
        )
    return runtime


def acquire_application_lock(root):
    """Acquire the two released instance lock names through the Qt 6 runtime.

    The file names, stale-lock policy and rollback remain owned by
    ``tools.update_apply.application_lock``. Only the QLockFile implementation
    is injected; the standalone transaction helper needs no GUI library.
    """

    from PySide6.QtCore import QLockFile

    module_root = Path(__file__).resolve().parent
    for candidate in (Path(root).resolve(), module_root, module_root.parent):
        if (candidate / "tools/update_apply.py").is_file():
            entry = str(candidate)
            if entry not in sys.path:
                sys.path.insert(0, entry)
            break
    else:
        raise ImportError("Cannot locate tools/update_apply.py for the shared instance lock")
    from tools.update_apply import application_lock

    return application_lock(root, lock_class=QLockFile)
