"""Actual Qt controls for slow, measured, cancelled, and failed operations."""
import os
from pathlib import Path
from threading import Event
from time import monotonic
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from quick_app import create_workspace
from operation_diagnostics import close_journal

assert os.environ.get('VWA_TESTING') == '1'
width = int(os.environ.get('VWA_QA_LAYOUT_WIDTH', '1440'))
viewport, scale = {1440: ((1440, 900), 100), 1920: ((1920, 1080), 125)}[width]
app = QGuiApplication([])
engine, window, owner, modes = create_workspace(app, viewport=viewport, scale=scale)
SECRET = 'LYRICS_OR_SIGNED_URL_DO_NOT_SHARE'
waiting, finishing, release = Event(), Event(), Event()


def wait(predicate, label, seconds=8):
    until = monotonic() + seconds
    while monotonic() < until:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(20)
    raise AssertionError((label, owner.activeOperations, owner.latestOperationIssue, owner.route))


def visual(name):
    pending = [window.contentItem()]
    while pending:
        item = pending.pop()
        if item.objectName() == name and item.isVisible():
            return item
        pending.extend(item.childItems())
    return None


def tap(name):
    wait(lambda: visual(name) is not None, name)
    node = visual(name)
    assert node.isEnabled(), name
    point = node.mapToScene(QPointF(node.width()/2, node.height()/2)).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    app.processEvents()


def component(_component_id, *, progress, check, **_options):
    progress(25, 100, '测试 · 传输中')
    if not waiting.wait(10):
        raise RuntimeError('test did not release first phase')
    progress(100, 100, '正在安装运行库')
    until = monotonic() + 10
    while not check() and monotonic() < until:
        release.wait(.02)
    if check():
        finishing.set()
        release.wait(10)
        raise InterruptedError('operation stopped')
    raise RuntimeError('stop was not delivered')


try:
    with patch('component_operations.install', side_effect=component):
        # Start through the same lane and expose it on a different workspace.
        from component_operations import _row
        row = _row('roformer', 'missing', detail='fixture', can_install=True)
        row['download_sources'] = (('魔搭', 'modelscope'),)
        owner.state.set_catalog('cpu', [row])
        assert owner.state.select('roformer')
        owner.setDownloadSource('modelscope')
        owner.installSelected()
        wait(lambda: owner.activeOperations and owner.activeOperations[0].get('fraction') == .25,
             'measured download')
        owner.navigate('publishing')
        wait(lambda: visual('workspaceOperationProgress') and
             abs(visual('workspaceOperationProgress').property('value') - .25) < .001, 'progress after route change')
        waiting.set()
        wait(lambda: owner.activeOperations[0].get('phase') == 'install'
             and 'fraction' not in owner.activeOperations[0], 'installation is not 100 percent done')
        start = owner.activeOperations[0]['elapsed']
        wait(lambda: owner.activeOperations[0]['elapsed'] > start and
             '00:0' in visual('operationTiming').property('text'), 'clock while worker is silent')
        tap('activityDiagnostics')
        wait(lambda: owner.support.diagnostics['text'] and not owner.support.diagnostics['busy'], 'async report')
        assert 'component running install' in owner.support.diagnostics['text']
        assert SECRET not in owner.support.diagnostics['text']
        assert visual('closeDiagnostics')
        tap('closeDiagnostics')
        assert owner.route == 'publishing'
        tap('cancelWorkspaceOperation')
        wait(finishing.is_set, 'reached cancellation cleanup')
        wait(lambda: '取消' in visual('operationHeadline').property('text'), 'cancelling visible')
        assert owner.activeOperations[0]['cancelling']
        release.set()
        wait(lambda: not any(row['kind'] == 'component' for row in owner.activeOperations),
             'finished cancellation')
        assert owner.operation['state'] == 'cancelled' and not owner.latestOperationIssue
        wait(lambda: not owner.loading, 'component refresh', seconds=25)

    def fail(_stop, _send):
        error = TimeoutError('https://huggingface.co/asset?signature='+SECRET)
        error.winerror = 10060
        raise error

    assert owner._work.start('component', fail, lambda _value, _error: None,
                             facts={'component':'roformer','source':'modelscope'})
    wait(lambda: owner.latestOperationIssue.get('code') == 'network_timeout', 'actionable failure')
    assert visual('operationHeadline') and '超时' in visual('operationHeadline').property('text')
    tap('activityDiagnostics')
    wait(lambda: not owner.support.diagnostics['busy'], 'refresh failed operation summary')
    text = owner.support.diagnostics['text']
    assert 'winerror=10060' in text and 'component=roformer' in text
    assert SECRET not in text and '?signature=' not in text
    tap('closeDiagnostics')
    tap('dismissOperationIssue')
    assert not owner.latestOperationIssue
    print(f'PASS {width}: live progress, install wait, navigation, cancellation, failure and private diagnostics')
finally:
    waiting.set()
    release.set()
    modes.close()
    owner.close()
    close_journal()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
