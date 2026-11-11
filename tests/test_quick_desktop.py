"""Load the integrated workspace at the two agreed sizes."""
from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication
from time import monotonic, sleep
from quick_app import create_workspace
from appearance_state import save_appearance
from i18n import current_language, tr


app = QGuiApplication([])
warnings = []
qInstallMessageHandler(lambda kind, _ctx, msg: warnings.append(msg)
                       if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg) else None)


def click(window, name):
    button = window.findChild(QObject, name)
    assert button and button.property('visible') and button.property('enabled'), name
    assert QMetaObject.invokeMethod(button, 'click'), name
    app.processEvents()


try:
    for viewport, scale in (((1440, 900), 100), ((1920, 1080), 125)):
        engine, window, owner, modes = create_workspace(app, viewport=viewport, scale=scale)
        try:
            loader = window.findChild(QObject, 'pageLoader')
            loaded = []
            for route in owner.state.routes:
                owner.state.navigate(route.page_id)
                owner.navigationChanged.emit()
                app.processEvents()
                page = loader.property('item')
                assert page is not None and page.width() > 0 and page.height() > 0, route.page_id
                loaded.append(route.page_id)
            for name in ('voiceCalibrationDialog', 'subtitleDialog', 'subtitleSourcesDialog'):
                assert window.findChild(QObject, name) is not None, name
            assert (window.width(), window.height()) == viewport
            print(f'PASS {viewport[0]}×{viewport[1]} at {scale}% · {", ".join(loaded)}', flush=True)
            if scale == 100:
                owner.support.showTutorial()
                app.processEvents()
                assert window.findChild(QObject, 'tutorialDialog').property('visible')
                assert owner.route == 'processing'
                click(window, 'tutorialNext')
                assert owner.route == 'components'
                click(window, 'tutorialPrevious')
                assert owner.route == 'processing'
                for route in ('components', 'tuning', 'audio_mixdown', 'video_mixdown', 'publishing', 'updates'):
                    click(window, 'tutorialNext')
                    assert owner.route == route
                group = window.findChild(QObject, 'tutorialQQGroup')
                assert group.property('visible') and '490619616' in group.property('text')
                click(window, 'tutorialSkip')
                assert not owner.support.startup['tour_open']
                print('PASS actual tutorial previous/next, all seven routes, QQ and skip', flush=True)
                completed = []
                assert owner._work.start('test-close', lambda stop, _send: stop.wait(3),
                                         lambda stopped, error: completed.append((stopped, error)))
                dialog = window.findChild(QObject, 'activeTaskExitDialog')
                for button in ('activeTaskKeepRunning', 'activeTaskCancelAndExit'):
                    assert not window.close()
                    app.processEvents()
                    assert dialog.property('visible') and window.isVisible()
                    click(window, button)
                until = monotonic() + 3
                while window.isVisible() and monotonic() < until:
                    app.processEvents()
                    sleep(.01)
                assert completed == [(True, None)] and not window.isVisible()
                print('PASS active task exit: keep running, then cancel and close after cleanup', flush=True)
        finally:
            modes.close()
            owner.close()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    for language in ('en', 'ja'):
        save_appearance({'ui_language': language})
        engine, window, owner, modes = create_workspace(app, viewport=(1440, 900), scale=100)
        try:
            assert current_language() == language
            owner.navigate('publishing')
            app.processEvents()
            fetch = window.findChild(QObject, 'publicationFetchBv')
            assert fetch.property('text') == tr('读取本家资料') != '读取本家资料'
            owner.navigate('audio_mixdown')
            app.processEvents()
            export = window.findChild(QObject, 'mixdownExport')
            # The compact and full layouts have different button captions.
            assert export.property('text') in {tr('导出'), tr('导出新 WAV')}
            assert export.property('text') not in {'导出', '导出新 WAV'}
            print('PASS visible publication and mixer labels:', language, flush=True)
        finally:
            modes.close()
            owner.close()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    save_appearance({'ui_language': 'zh-CN'})
    assert not warnings, warnings
finally:
    qInstallMessageHandler(None)
