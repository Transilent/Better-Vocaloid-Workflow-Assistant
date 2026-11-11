"""Qt 6 desktop entry for the preparation/Components migration slice."""

import argparse
import ctypes
import os
from pathlib import Path
import sys

from quick_runtime import acquire_application_lock, configure_quick_runtime


ROOT = Path(__file__).resolve().parent


def _system_reduce_motion():
    if os.name != 'nt':
        return False
    animation = ctypes.c_int()
    ok = ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(animation), 0)
    return bool(ok and not animation.value)


def create_workspace(app, *, root=ROOT, show=True, viewport=None, scale=None):
    """Construct the same window for interactive launch, smoke and native QA."""
    from PySide6.QtCore import QSize, QUrl
    from PySide6.QtGui import QFont, QIcon
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle

    from appearance_state import load_appearance
    from i18n import set_language
    from quick_bridge import QuickBridge
    from quick_window_modes import WindowModes

    appearance = load_appearance()
    set_language(appearance['ui_language'])
    QQuickStyle.setStyle('Basic')
    app.setFont(QFont('Microsoft YaHei UI'))
    assets = root / 'assets'
    for name in ('app.ico', 'app.png', 'moonlight-background.png'):
        if not (assets / name).is_file():
            raise FileNotFoundError(f'Missing Quick asset: {assets / name}')
    app.setWindowIcon(QIcon(str(assets / 'app.ico')))
    engine = QQmlApplicationEngine()
    controller = QuickBridge()
    try:
        display = app.primaryScreen().availableGeometry()
        initial = viewport or (max(1, min(1440, display.width() - 42)),
                               max(1, min(900, display.height() - 54)))
        engine.setInitialProperties({
            'controller': controller,
            'assets': QUrl.fromLocalFile(str(assets) + '/'),
            'initialWidth': initial[0],
            'initialHeight': initial[1],
            'initialScale': (scale or appearance['ui_scale']) / 100,
            'initialReducedMotion': appearance['reduce_motion'] or _system_reduce_motion(),
            'showWindow': show,
            'useArtwork': appearance['material'] == 'glass',
        })
        engine.load(QUrl.fromLocalFile(str(root / 'quick_ui/Main.qml')))
        if not engine.rootObjects():
            raise RuntimeError('Quick workspace could not load quick_ui/Main.qml.')
        window = engine.rootObjects()[0]
        modes = WindowModes(app, window, QSize(*(viewport or (1440, 900))),
                            fixture_viewport=viewport is not None) if show else None
        return engine, window, controller, modes
    except BaseException:
        controller.close()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--quick-runtime', type=Path)
    parser.add_argument('--software', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--smoke-test', action='store_true')
    parser.add_argument('--desktop', action='store_true', help=argparse.SUPPRESS)
    options = parser.parse_args(argv)
    runtime = configure_quick_runtime(ROOT, options.quick_runtime, software=options.software)

    from PySide6.QtCore import QCoreApplication, QEvent, QTimer, QtMsgType, qInstallMessageHandler, qVersion
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication([sys.argv[0]])
    app.setApplicationName('VWA')
    warnings = []

    def message_handler(kind, _context, message):
        if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            warnings.append(message)
            print(message, file=sys.stderr, flush=True)

    if options.check or options.smoke_test:
        qInstallMessageHandler(message_handler)
    guard = None if options.check else acquire_application_lock(ROOT)
    if not options.check and guard is None:
        print('助手已在运行，请使用已打开的窗口。', flush=True)
        return 2
    engine = controller = modes = None
    try:
        engine, window, controller, modes = create_workspace(app, show=not options.check)
        print(f'VWA Quick · Qt {qVersion()} · {runtime}', flush=True)
        if options.check:
            controller.navigate('components')
            app.processEvents()
            if warnings:
                raise RuntimeError('Quick runtime or QML check reported warnings.')
            print('Quick runtime, assets and both pages are ready.', flush=True)
            return 0
        controller.refresh()
        if options.smoke_test:
            controller.navigate('components')
            deadline = QTimer()
            deadline.setSingleShot(True)
            deadline.timeout.connect(lambda: app.exit(3))
            deadline.start(12000)

            def complete_smoke():
                if controller.loading:
                    return
                if warnings or controller.notice or controller.componentModel.rowCount() < 1:
                    app.exit(3)
                    return
                print(f'Quick GUI ready; {controller.componentModel.rowCount()} component rows loaded.', flush=True)
                app.quit()

            controller.activityChanged.connect(complete_smoke)
            QTimer.singleShot(150, complete_smoke)
        return app.exec()
    finally:
        if modes is not None:
            modes.close()
        if controller is not None:
            controller.close()
        if engine is not None:
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        if guard is not None:
            guard.unlock()
        if options.check or options.smoke_test:
            qInstallMessageHandler(None)
