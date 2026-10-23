"""Full-page publication navigation, stale-cover handling and accessible layouts."""
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PyQt5.QtCore import QPoint,Qt
from PyQt5.QtGui import QColor,QPainter,QPixmap,QPalette
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from app import Window
from common import atomic_json, workspace_state
from desktop_theme import apply_theme

app=QApplication([])
window=Window();window.show();window.navigate(3);app.processEvents()
tab=window.publishing

def visible_inside(control):
    assert control.isVisible()
    assert window.rect().contains(control.mapTo(window,control.rect().topLeft()))
    assert window.rect().contains(control.mapTo(window,control.rect().bottomRight()))

with tempfile.TemporaryDirectory(dir=ROOT/'work') as tmp:
    scratch=Path(tmp);job=scratch/'source';job.mkdir()
    atomic_json(job/'request.json',{'bv':None})
    atomic_json(job/'source-info.json',{'kind':'local','bvid':None,'title':'测试歌曲','owner':{'name':''}})
    (job/'发布简介草稿.txt').write_text('本家：测试来源\nSTAFF：测试作者',encoding='utf-8')
    video=scratch/'finished.mp4';video.write_bytes(b'validation fixture')
    cover=QPixmap(1600,900);cover.fill(QColor('#cdebdc'))
    painter=QPainter(cover);painter.fillRect(0,0,600,900,QColor('#83b6b2'));painter.fillRect(900,0,700,900,QColor('#e1eee3'));painter.end()
    cover_path=scratch/'cover.png';assert cover.save(str(cover_path))
    # Reading publication credits/cover must not download or decode full media.
    downloads=[]
    def metadata(bv, tools=None):
        return {'bvid':bv,'title':'测试歌曲','desc':'','owner':{'name':'测试作者'},'duration':3}
    def media(destination,request):
        downloads.append(destination)
        (destination/'video').mkdir(exist_ok=True)
        (destination/'video/source.mp4').write_bytes(b'x'*2048)
        atomic_json(destination/'download-report.json',{})
    from pipeline import STEPS
    with patch.object(window,'output_parent',return_value=scratch/'jobs'), \
         patch('pipeline.get_view',side_effect=metadata), \
         patch('pipeline.STEPS',[(k,t,media if k=='download' else f) for k,t,f in STEPS]), \
         patch('cover_art.job_cover',return_value=(cover_path,{'url':'fixture'})), \
         patch.object(tab,'start_task',side_effect=lambda function,ready:ready(function())):
        before_state = workspace_state()
        tab.bv.setText('BV1000000000');tab.fetch_source()
        assert workspace_state() == before_state, 'Fetching publication metadata must not change processing output preferences'
        source_job=Path(tab.job.text())
        assert not downloads and not (source_job/'video').exists() and not (source_job/'audio').exists()
        assert not (source_job/'midi').exists() and (source_job/'source-info.json').exists()
        tab.video.setText(str(video));tab.select_step(1)
        QTest.mouseClick(tab.workspace.next,Qt.LeftButton)
        assert tab.workspace.stack.currentIndex()==2,tab.status.text()
        tab.select_step(0)
        tab.cover_mode.setCurrentIndex(tab.cover_mode.findData('frame'))
        tab.validate_cover_choice()
        with patch('publishing_ui.extract_cover',side_effect=lambda *args:None):
            tab.preview_cover();tab.preview_cover()
        assert len(downloads)==1 and not (source_job/'audio').exists()
        state=json.loads((source_job/'status.json').read_text(encoding='utf-8'))
        state['state']='done';atomic_json(source_job/'status.json',state)
        from pipeline import ensure_source_video
        ensure_source_video(source_job)
        assert json.loads((source_job/'status.json').read_text(encoding='utf-8'))['state']=='done'
    tab.video.clear();tab.job.clear()
    QTest.mouseClick(tab.workspace.next,Qt.LeftButton)
    assert tab.workspace.stack.currentIndex()==0 and '成品视频' in tab.status.text()
    tab.video.setText(str(video))
    QTest.mouseClick(tab.workspace.next,Qt.LeftButton)
    assert tab.workspace.stack.currentIndex()==0 and '来源任务' in tab.status.text()
    # Numbered navigation must not resolve an empty source to the application root.
    tab.cover_mode.setCurrentIndex(tab.cover_mode.findData('original'))
    QTest.mouseClick(tab.workspace.buttons[1],Qt.LeftButton);app.processEvents()
    QTest.mouseClick(tab.workspace.next,Qt.LeftButton)
    assert tab.workspace.stack.currentIndex()==0,tab.status.text()
    assert '来源任务' in tab.status.text() and '[Errno' not in tab.status.text()
    with patch.object(tab,'start_task') as worker:
        tab.select_step(1);tab.preview_cover()
        assert tab.workspace.stack.currentIndex()==0 and not worker.called
        tab.select_step(3);tab.prepare_bundle()
        assert tab.workspace.stack.currentIndex()==0 and not worker.called
    tab.select_step(2);QTest.mouseClick(tab.workspace.next,Qt.LeftButton)
    assert tab.workspace.stack.currentIndex()==0 and '来源任务' in tab.status.text()
    # A moved task and malformed metadata have recoverable messages too.
    tab.job.setText(str(scratch/'missing-source'))
    tab.select_step(1);QTest.mouseClick(tab.workspace.next,Qt.LeftButton)
    assert tab.workspace.stack.currentIndex()==0 and '重新选择' in tab.status.text()
    assert '[Errno' not in tab.status.text()
    invalid=scratch/'invalid-source';invalid.mkdir()
    (invalid/'source-info.json').write_text('{invalid json',encoding='utf-8')
    tab.job.setText(str(invalid))
    tab.select_step(1);QTest.mouseClick(tab.workspace.next,Qt.LeftButton)
    assert tab.workspace.stack.currentIndex()==0 and '来源资料' in tab.status.text()
    assert 'Expecting' not in tab.status.text()
    # Restoring a moved task must report the missing state rather than hide it.
    recovery=scratch/'recovery';recovery.mkdir()
    atomic_json(recovery/'publish-ui.json',{'video':str(video),'job':str(scratch/'missing-source')})
    from publishing_ui import PublishingTab
    with patch('publishing_ui.ROOT',recovery):
        restored=PublishingTab(window)
        assert restored.video.text()==str(video) and not restored.job.text()
        assert '未能恢复' in restored.status.text()
        restored.select_step(1);restored.next_step()
        assert restored.workspace.stack.currentIndex()==0 and '来源任务' in restored.status.text()
        restored.timer.stop();restored.close();restored.deleteLater()
    tab.load_job(job)
    QTest.mouseClick(tab.workspace.next,Qt.LeftButton);app.processEvents()
    assert tab.workspace.stack.currentIndex()==1
    assert not tab.video.isVisible() and tab.cover_mode._vwa_choice.isVisible()
    assert not tab.frame_panel.isVisible() and tab.custom_cover_panel.isVisible()
    QTest.mouseClick(tab.workspace.next,Qt.LeftButton)
    assert tab.workspace.stack.currentIndex()==1 and '封面图片' in tab.status.text()
    tab.cover_file.setText(str(cover_path));tab.select_step(1);app.processEvents()
    assert not tab.cover_preview.original.isNull()
    assert tab.cover_preview.width()>=300 and tab.cover_preview.height()>=230
    shown=tab.cover_preview.pixmap()
    assert abs(shown.width()/shown.height()-16/9)<.01
    tab.job.setText(str(scratch/'other-source'))
    assert tab.cover_preview.original.isNull()
    tab.job.setText(str(job));tab.select_step(1);app.processEvents()
    # A changed source must not leave the previous image on screen.
    tab.cover_mode.setCurrentIndex(tab.cover_mode.findData('frame'));app.processEvents()
    assert tab.frame_panel.isVisible() and not tab.custom_cover_panel.isVisible()
    assert tab.cover_preview.original.isNull()
    QTest.mouseClick(tab.workspace.next,Qt.LeftButton)
    assert tab.workspace.stack.currentIndex()==1 and '原视频' in tab.status.text()
    tab.cover_mode.setCurrentIndex(tab.cover_mode.findData('custom'));tab.select_step(1)
    QTest.mouseClick(tab.workspace.next,Qt.LeftButton);app.processEvents()
    assert tab.workspace.stack.currentIndex()==2
    tab.cover_file.clear()
    QTest.mouseClick(tab.workspace.next,Qt.LeftButton)
    assert tab.workspace.stack.currentIndex()==1 and '封面图片' in tab.status.text()
    tab.cover_file.setText(str(cover_path));tab.select_step(2)
    assert all(fields['description'].isVisible() for fields in tab.fields.values())
    tab.fields['xiaohongshu']['title'].setText('长'*21)
    QTest.mouseClick(tab.workspace.next,Qt.LeftButton)
    assert tab.workspace.stack.currentIndex()==2 and '20' in tab.status.text()
    assert tab.fields['xiaohongshu']['counter'].property('role')=='error'
    tab.fields['xiaohongshu']['title'].setText('测试翻唱')
    QTest.mouseClick(tab.workspace.next,Qt.LeftButton);app.processEvents()
    assert tab.workspace.stack.currentIndex()==3
    visible_inside(tab.prepare_button)
    assert not tab.workspace.next.isVisible()
    assert all(actions['login'].isVisible() for actions in tab.account_actions.values())
    # Background asset generation locks navigation, without losing the selected step.
    tab.set_busy(True)
    QTest.mouseClick(tab.workspace.buttons[0],Qt.LeftButton)
    assert tab.workspace.stack.currentIndex()==3 and not tab.prepare_button.isEnabled()
    tab.set_busy(False)
    QTest.mouseClick(tab.workspace.previous,Qt.LeftButton);app.processEvents()
    assert tab.workspace.stack.currentIndex()==2 and not tab.prepare_button.isVisible()
    assert tab.workspace.next.isVisible()
    tab.select_step(-1);assert tab.workspace.stack.currentIndex()==2
    tab.select_step(100);assert tab.workspace.stack.currentIndex()==2
    # Progress belongs to its platform; a missing state key is valid with a message.
    atomic_json(scratch/'bilibili_上传状态.json',{'message':'等待确认标题与封面'})
    fake=SimpleNamespace(vf_location=scratch,poll=lambda:None)
    tab.processes['bilibili']=fake
    tab.poll_browser();assert tab.account_status['bilibili'].text()=='等待确认标题与封面'
    assert not tab.account_actions['bilibili']['login'].isEnabled()
    assert tab.account_actions['bilibili']['stop'].isEnabled()
    tab.stop_browser('bilibili');assert (scratch/'bilibili.stop').is_file()
    tab.processes.clear()
    # The fixed footer stays reachable at the supported minimum size in both themes.
    for theme in ('light','dark'):
        apply_theme(window,theme)
        for size in ((1600,1000),(1000,680)):
            window.resize(*size);app.processEvents()
            assert (window.width(),window.height())==size,(size,window.size())
            for step in range(4):
                tab.select_step(step);app.processEvents()
                visible_inside(tab.workspace.previous)
                visible_inside(tab.workspace.buttons[-1])
                visible_inside(tab.prepare_button if step==3 else tab.workspace.next)
                assert not window.testAttribute(Qt.WA_TranslucentBackground)
                assert not window.windowFlags()&Qt.FramelessWindowHint
                assert window.windowOpacity()==1.0
            background=window.grab().toImage()
            point=window.pages.mapTo(window,QPoint(8,8))
            color=background.pixelColor(point)
            assert color.alpha()==255, 'Application-internal glass keeps an opaque standard system window'
            assert window._vwa_backdrop._pixmap is not None
            window.navigate(0);app.processEvents();visible_inside(window.start)
            window.navigate(3)
    window.resize(1600,1000);apply_theme(window,'light');app.processEvents()
    window.navigate(0)
    window.step_cards['reference'].toggle.setChecked(True);app.processEvents()
    assert window.lyrics.width()>=300 and window.lyrics.height()>=150
    language_view=getattr(window.language,'_vwa_choice',window.language)
    assert language_view.isVisible() and language_view.height()>=42 and window.device_button.isEnabled()
    window.devices_dialog.show();app.processEvents()
    device_view=getattr(window.midi_device,'_vwa_choice',window.midi_device)
    assert device_view.isVisible() and device_view.height()>=42
    window.midi_device.setCurrentIndex(window.midi_device.findData('dml'))
    assert window.options()['midi_device']=='dml'
    window.devices_dialog.close()
    window.step_cards['reference'].toggle.setChecked(False)
    # Documentation captures contain no account state or personal input paths.
    tab.video.clear();tab.job.clear();tab.bv.clear()
    tab.cover_mode.setCurrentIndex(tab.cover_mode.findData('original'))
    tab.cover_preview.setPixmap(cover);tab.cover_detail.setText('完整原图预览 · 1600 × 900')
    tab.status.setText('准备就绪 · 检查成品、封面与文案')
    for status in tab.account_status.values(): status.setText('登录后关闭登录窗口，再开始上传。已有登录会话会自动复用。')
    tab.refresh_browser_controls()
    tab.select_step(1);window.navigate(3);app.processEvents()
    window.grab().save(str(ROOT/'docs/images/publishing-cover.png'))
    tab.select_step(3);app.processEvents()
    window.grab().save(str(ROOT/'docs/images/publishing-upload.png'))

window.close();app.processEvents()
print('PASS: four publication steps, missing-source recovery, numbered navigation, stale-state restore, cover ratios, busy locks, platform status, minimum-size footers, larger controls')
