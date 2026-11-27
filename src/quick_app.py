"""Qt 6 desktop entry for the preparation and management workspaces."""

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from quick_runtime import acquire_application_lock, configure_quick_runtime


ROOT = Path(__file__).resolve().parent


def _ipc_server_name(root=ROOT) -> str:
    digest = hashlib.sha256(os.path.normcase(str(Path(root).resolve())).encode('utf-8')).hexdigest()[:16]
    return f'vwa-instance-{digest}'


def _notify_running_instance(root=ROOT) -> bool:
    """Ask the existing desktop instance to restore & raise its window."""
    try:
        from PySide6.QtNetwork import QLocalSocket
    except ImportError:
        return False
    sock = QLocalSocket()
    sock.connectToServer(_ipc_server_name(root))
    if not sock.waitForConnected(800):
        return False
    try:
        sock.write(b'activate\n')
        sock.flush()
        sock.waitForBytesWritten(500)
        return True
    finally:
        sock.disconnectFromServer()


def _start_instance_server(window, root=ROOT):
    """Listen on a per-installation local socket so second launches raise the window."""
    try:
        from PySide6.QtNetwork import QLocalServer
    except ImportError:
        return None
    name = _ipc_server_name(root)
    QLocalServer.removeServer(name)
    server = QLocalServer()
    if not server.listen(name):
        return None

    def _on_connection():
        while server.hasPendingConnections():
            client = server.nextPendingConnection()
            if client is None:
                continue
            client.waitForReadyRead(300)
            payload = bytes(client.readAll()).decode('utf-8', errors='ignore').strip()
            client.disconnectFromServer()
            client.deleteLater()
            if 'activate' in payload and window is not None:
                try:
                    window.showNormal()
                    window.raise_()
                    window.requestActivate()
                except RuntimeError:
                    pass

    server.newConnection.connect(_on_connection)
    return server


def _startup_probe_path(root=ROOT) -> Path:
    return Path(root).resolve() / 'cache/startup-probe.json'


def _begin_graphics_probe(root=ROOT, *, requested_software: bool = False, check_mode: bool = False):
    """Detect prior pre-first-frame crashes and auto-fallback to software Qt Quick rendering."""
    from appearance_state import load_appearance
    appearance = load_appearance()
    if requested_software or appearance.get('render_backend') == 'software':
        os.environ.pop('VWA_RENDER_FALLBACK', None)
        return True, False
    if check_mode or os.environ.get('VWA_TESTING') == '1':
        os.environ.pop('VWA_RENDER_FALLBACK', None)
        return False, False
    probe = _startup_probe_path(root)
    auto_fallback = False
    try:
        if probe.is_file():
            prev = json.loads(probe.read_text(encoding='utf-8-sig'))
            age = time.time() - float(prev.get('ts', 0))
            if prev.get('state') == 'starting' and 0 <= age <= 3600:
                auto_fallback = True
    except (OSError, ValueError, TypeError):
        pass
    try:
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text(json.dumps({
            'state': 'fallback_active' if auto_fallback else 'starting',
            'pid': os.getpid(),
            'ts': time.time(),
        }), encoding='utf-8')
    except OSError:
        pass
    if auto_fallback:
        os.environ['VWA_RENDER_FALLBACK'] = '1'
    else:
        os.environ.pop('VWA_RENDER_FALLBACK', None)
    return auto_fallback, auto_fallback


def _clear_graphics_probe(root=ROOT):
    try:
        _startup_probe_path(root).unlink(missing_ok=True)
    except OSError:
        pass


def _system_reduce_motion():
    if os.name != 'nt':
        return False
    animation = ctypes.c_int()
    ok = ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(animation), 0)
    return bool(ok and not animation.value)


def create_workspace(app, *, root=ROOT, show=True, viewport=None, scale=None):
    """Construct the same window for interactive launch, smoke and native QA."""
    from PySide6.QtCore import QSize, Qt, QUrl
    from PySide6.QtGui import QFont, QIcon
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle

    from appearance_state import load_appearance
    from i18n import set_language
    from quick_bridge import QuickBridge
    from quick_window_modes import WindowModes

    appearance = load_appearance()
    set_language(appearance['ui_language'])
    if QQuickStyle.name() != 'Basic':
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
        engine.rootContext().setContextProperty('quickTranslation', controller)
        display = app.primaryScreen().availableGeometry()
        initial = viewport or (max(1, min(1440, display.width() - 42)),
                               max(1, min(900, display.height() - 54)))
        restored_geo = None
        if show and viewport is None and os.environ.get('VWA_TESTING') != '1':
            from workspace_ui_state import clamp_window_geometry, load_ui_state
            ui_state = load_ui_state(root)
            screens = [
                (s.availableGeometry().x(), s.availableGeometry().y(),
                 s.availableGeometry().width(), s.availableGeometry().height())
                for s in app.screens()
            ]
            restored_geo = clamp_window_geometry(ui_state.get('window'), screens)
            if restored_geo is not None:
                initial = (restored_geo['width'], restored_geo['height'])
        engine.setInitialProperties({
            'controller': controller,
            'assets': QUrl.fromLocalFile(str(assets) + '/'),
            'initialWidth': initial[0],
            'initialHeight': initial[1],
            'initialScale': (scale or 0) / 100,
            'initialReducedMotion': _system_reduce_motion(),
            'systemDark': app.styleHints().colorScheme() == Qt.ColorScheme.Dark,
            'showWindow': show,
        })
        engine.load(QUrl.fromLocalFile(str(root / 'quick_ui/Main.qml')))
        if not engine.rootObjects():
            raise RuntimeError('Quick workspace could not load quick_ui/Main.qml.')
        window = engine.rootObjects()[0]
        if restored_geo is not None:
            try:
                window.setPosition(restored_geo['x'], restored_geo['y'])
                if restored_geo.get('maximized'):
                    window.showMaximized()
            except RuntimeError:
                pass
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
    from operation_diagnostics import journal_for
    journal = journal_for(ROOT)
    check_mode = options.check or options.smoke_test
    use_software, auto_fallback = _begin_graphics_probe(
        ROOT, requested_software=options.software, check_mode=check_mode,
    )
    runtime = configure_quick_runtime(ROOT, options.quick_runtime, software=use_software)

    from PySide6.QtCore import QObject, QCoreApplication, QEvent, QTimer, QtMsgType, qInstallMessageHandler, qVersion
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication([sys.argv[0]])
    app.setApplicationName('VWA')
    warnings = []
    qt_raw_count = 0
    previous_exception_hook = sys.excepthook

    def message_handler(kind, _context, message):
        nonlocal qt_raw_count
        level = {QtMsgType.QtWarningMsg: 'warning', QtMsgType.QtCriticalMsg: 'critical',
                 QtMsgType.QtFatalMsg: 'fatal'}.get(kind)
        if level:
            if check_mode:
                warnings.append(message)
            else:
                journal.event('qt_warning', kind='qt', code='qt', level=level)
        if sys.stderr is not None and (check_mode or level == 'fatal' or (level and qt_raw_count < 30)):
            qt_raw_count += 1
            print(message, file=sys.stderr, flush=True)

    def exception_hook(error_type, error, traceback):
        journal.problem('qt', error, code='callback')
        previous_exception_hook(error_type, error, traceback)

    guard = None if options.check else acquire_application_lock(ROOT)
    if not options.check and guard is None:
        activated = _notify_running_instance(ROOT)
        _clear_graphics_probe(ROOT)
        print('助手已在运行，已唤醒已有窗口。' if activated else '助手已在运行，请使用已打开的窗口。', flush=True)
        return 2
    engine = controller = modes = ipc_server = None
    exit_code = None
    qInstallMessageHandler(message_handler)
    if not check_mode:
        sys.excepthook = exception_hook
        journal.event('session_start', kind='startup')
    try:
        engine, window, controller, modes = create_workspace(app, show=not options.check)
        if not check_mode:
            ipc_server = _start_instance_server(window, ROOT)
            QTimer.singleShot(3000, lambda: _clear_graphics_probe(ROOT))
            if auto_fallback:
                journal.event('qt_warning', kind='startup', code='device', level='warning')
        print(f'VWA Quick · Qt {qVersion()} · {runtime}', flush=True)
        if options.check:
            loader = window.findChild(QObject, 'pageLoader')
            for spec in controller.state.routes:
                # Load each view without starting a task scan or a startup check.
                controller.state.navigate(spec.page_id)
                controller.navigationChanged.emit()
                app.processEvents()
                if loader is None or loader.property('item') is None:
                    raise RuntimeError(f'Quick workspace could not load route: {spec.page_id}')
            if warnings:
                raise RuntimeError('Quick runtime or QML check reported warnings.')
            print(f'Quick runtime, assets, {len(controller.state.routes)} workspaces and startup dialogs are ready.', flush=True)
            return 0
        controller.refresh()
        if options.smoke_test:
            controller.navigate('components')
            deadline = QTimer()
            deadline.setSingleShot(True)
            deadline.timeout.connect(lambda: app.exit(3))
            deadline.start(12000)
            smoke_finished = False

            def complete_smoke():
                nonlocal smoke_finished
                if smoke_finished or controller.loading:
                    return
                smoke_finished = True
                deadline.stop()
                if warnings or controller.notice or controller.componentModel.rowCount() < 1:
                    app.exit(3)
                    return
                print(f'Quick GUI ready; {controller.componentModel.rowCount()} component rows loaded.', flush=True)
                app.quit()

            controller.activityChanged.connect(complete_smoke)
            QTimer.singleShot(150, complete_smoke)
        else:
            if os.environ.get('VWA_TESTING') != '1':
                from workspace_ui_state import load_last_route
                import startup_preferences
                if not startup_preferences.tour_due(controller.support.state.preferences):
                    saved_route = load_last_route(ROOT, default='processing')
                    if saved_route != controller.route:
                        controller.navigate(saved_route)
            QTimer.singleShot(0, controller.support.begin_startup)
        exit_code = app.exec()
        return exit_code
    finally:
        if not check_mode and window is not None and os.environ.get('VWA_TESTING') != '1':
            try:
                from PySide6.QtGui import QWindow
                from workspace_ui_state import save_ui_state
                maximized = window.visibility() == QWindow.Visibility.Maximized
                save_ui_state(
                    {
                        'window': {
                            'x': int(window.x()),
                            'y': int(window.y()),
                            'width': int(window.width()),
                            'height': int(window.height()),
                            'maximized': bool(maximized),
                        },
                        'last_route': str(controller.route) if controller is not None else 'processing',
                    },
                    root=ROOT,
                )
            except (OSError, RuntimeError, ValueError, TypeError):
                pass
        if ipc_server is not None:
            ipc_server.close()
        if modes is not None:
            modes.close()
        if controller is not None:
            controller.close()
        if engine is not None:
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        if guard is not None:
            guard.unlock()
        sys.excepthook = previous_exception_hook
        qInstallMessageHandler(None)
        if not check_mode and exit_code is not None:
            journal.event('session_end', kind='startup', state='succeeded' if not exit_code else 'failed',
                          exit_code=exit_code & 0xffffffff)
