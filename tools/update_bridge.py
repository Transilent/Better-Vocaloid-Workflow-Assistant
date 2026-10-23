"""One-time GUI migration for clients that cannot read a multi-runtime catalog.

The packaged launcher supplies this file and the verified installer alongside
each other. No installed application module is imported by this tool.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update_apply as helper


def check_installation(root, source='auto', reader=None):
    root = Path(root).resolve()
    fingerprint = helper.sha(root/'dependencies/manifest.json')
    try:
        installed = json.loads((root/'version.json').read_text(encoding='utf-8-sig'))
        if not isinstance(installed, dict): installed = {}
    except (OSError,ValueError): installed = {}
    current = installed.get('version')
    if not isinstance(current,str) or not re.fullmatch(r'\d+\.\d+\.\d+',current): current = None
    catalog = (reader or helper.read_release_catalog)(source)
    version = catalog.get('version') if isinstance(catalog,dict) else None
    if not isinstance(version,str) or not re.fullmatch(r'\d+\.\d+\.\d+',version):
        raise ValueError('发布清单中没有有效的正式版本。')
    candidate = tuple(map(int,version.split('.')))
    baseline = tuple(map(int,current.split('.'))) if current else None
    if baseline is not None and candidate < baseline:
        raise ValueError('发布版本早于本地版本，兼容工具不会降级安装。')
    available = baseline is None or candidate > baseline or bool(installed.get('upgrade_pending'))
    route = helper.select_update_route(catalog,fingerprint) if available else None
    if available and route is None:
        raise ValueError('此运行库尚无匹配的增量迁移路径，请查看发布说明。')
    return {'version':version,'installed':current,'available':available,'route':route,
            'notes':catalog.get('release_notes',''),'root':str(root)}


def prepare(result, source='auto', progress=lambda *args:None, check=None, downloader=None):
    if not result['available'] or result.get('route') is None:
        raise ValueError('没有需要安装的更新。')
    if reason := helper.installation_busy(result['root']):
        raise RuntimeError(reason)
    return helper.prepare_catalog_update(result['version'],result['route'],result['root'],progress,check,source,downloader)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--smoke-test', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    if not (root/'dependencies/manifest.json').is_file():
        raise ValueError('请选择包含 dependencies 文件夹的助手目录。')
    if args.check:
        print(json.dumps({'runtime_sha256':helper.sha(root/'dependencies/manifest.json'),
                          'console_attached':bool(ctypes.windll.kernel32.GetConsoleWindow()) if os.name=='nt' else False,
                          'catalog_protocol':2,'network_requested':False}),flush=True)
        return 0
    from PyQt5.QtCore import QThread,QTimer,pyqtSignal,QLockFile
    from PyQt5.QtGui import QFontDatabase,QFont
    from PyQt5.QtWidgets import QApplication,QComboBox,QDialog,QHBoxLayout,QLabel,QProgressBar,QPushButton,QVBoxLayout
    application = QApplication([])
    # The embedded Qt runtime may not discover system fonts, especially when
    # launched offscreen. Register an installed font; keep Qt's fallback if absent.
    font_file = Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts/msyh.ttc'
    families = QFontDatabase.applicationFontFamilies(QFontDatabase.addApplicationFont(str(font_file))) if font_file.is_file() else []
    if families: application.setFont(QFont(families[0],11))
    guard = helper.application_lock(root)
    if guard is None:
        raise RuntimeError('助手正在运行，请关闭助手后再打开兼容更新工具。')

    class Worker(QThread):
        ready=pyqtSignal(object);failed=pyqtSignal(str);progress=pyqtSignal(int,int,str)
        def __init__(self, result, source):
            super().__init__();self.result,self.source=result,source;self.cancelled=threading.Event();self.last_check=0.;self.reason=''
        def cancelled_or_busy(self):
            if time.monotonic()-self.last_check >= .5:
                self.last_check=time.monotonic();self.reason=helper.installation_busy(root)
            return self.cancelled.is_set() or bool(self.reason)
        def run(self):
            try:
                if self.result is None:
                    result=check_installation(root,self.source)
                    if self.cancelled.is_set():raise InterruptedError('已取消检查更新。')
                    self.ready.emit(result)
                else:
                    self.ready.emit(prepare(self.result,self.source,self.progress.emit,self.cancelled_or_busy))
            except Exception as error:
                traceback.print_exc()
                self.failed.emit(self.reason or str(error))

    class Window(QDialog):
        def __init__(self):
            super().__init__();self.worker=None;self.release=None;self.plan=None;self.close_requested=False;self.install_started=False
            self.setWindowTitle('术力口工作流助手 · 兼容更新');self.resize(720,440)
            self.setStyleSheet('QDialog{background:#f3f6f3} QWidget{color:#263d32;font:18px "Microsoft YaHei"} QPushButton,QComboBox{padding:10px 16px} QProgressBar{min-height:24px}')
            body=QVBoxLayout(self);body.setContentsMargins(28,24,28,24);body.setSpacing(20)
            title=QLabel('升级现有助手，保留任务和模型');title.setStyleSheet('font-size:24px;font-weight:600');body.addWidget(title)
            info=QLabel('本工具为旧版更新器补充迁移入口。下载并校验所需增量后，点击安装。\n请先关闭此目录的助手、音乐任务和上传窗口。');info.setWordWrap(True);body.addWidget(info)
            location=QLabel(str(root));location.setWordWrap(True);body.addWidget(location)
            self.source=QComboBox()
            for title,key in helper.DOWNLOAD_SOURCE_OPTIONS:self.source.addItem(title,key)
            try:
                config=json.loads((root/'config.json').read_text(encoding='utf-8-sig'));saved=config.get('download_source') if isinstance(config,dict) else None
                if saved=='official':saved='direct'
                self.source.setCurrentIndex(max(0,self.source.findData(saved)))
            except (OSError,ValueError):pass
            body.addWidget(self.source)
            self.bar=QProgressBar();self.bar.setRange(0,1000);body.addWidget(self.bar)
            self.message=QLabel('准备就绪。点击检查更新，不需要重新下载完整便携包。');self.message.setWordWrap(True);body.addWidget(self.message,1)
            row=QHBoxLayout();self.action=QPushButton('检查更新');self.action.clicked.connect(self.start);row.addWidget(self.action)
            self.cancel=QPushButton('关闭');self.cancel.clicked.connect(self.stop_or_close);row.addWidget(self.cancel);body.addLayout(row)
        def start(self):
            if self.worker and self.worker.isRunning():return
            if reason:=helper.installation_busy(root):self.message.setText(reason);return
            if self.plan:
                try:
                    python=root/'dependencies/vocal2midi/python/pythonw.exe'
                    subprocess.Popen([str(python),'-B',str(self.plan.parent/'apply_helper.py'),'--plan',str(self.plan),
                        '--parent',str(os.getpid()),'--launcher-parent',os.environ.get('VWA_LAUNCHER_PID',os.environ.get('BVWA_LAUNCHER_PID','0'))],cwd=root,
                        env=dict(os.environ,PYTHONUTF8='1',PYTHONNOUSERSITE='1'),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                except OSError as error:self.message.setText('未能启动安装程序：'+str(error));return
                self.install_started=True;application.quit();return
            self.close_requested=False;self.action.setEnabled(False);self.source.setEnabled(False);self.cancel.setText('取消')
            self.message.setText('正在检查更新…' if self.release is None else '正在准备增量更新，原安装保持不变…')
            self.worker=Worker(self.release,self.source.currentData())
            self.worker.ready.connect(self.ready);self.worker.failed.connect(self.failed);self.worker.progress.connect(self.progress)
            self.worker.finished.connect(self.finished);self.worker.start()
        def ready(self,value):
            if self.close_requested:return
            if isinstance(value,dict):
                self.release=value if value['available'] else None
                self.action.setText('下载更新' if value['available'] else '重新检查')
                self.message.setText(('可升级到 '+value['version']+'，任务、设置和模型会保留。') if value['available'] else '已是最新正式版 '+value['version'])
            else:
                self.plan=Path(value);self.action.setText('安装并启动助手');self.bar.setValue(1000)
                self.message.setText('增量文件已全部校验。点击安装后本工具会关闭，完成后自动启动助手。')
        def failed(self,text):self.message.setText(text+'\n可切换下载源重试。')
        def progress(self,done,total,text):
            self.bar.setValue(int(done*1000/total) if total else 0);self.message.setText(text)
        def finished(self):
            self.action.setEnabled(True);self.source.setEnabled(True);self.cancel.setText('关闭')
            if self.close_requested:self.close()
        def stop_or_close(self):
            if self.worker and self.worker.isRunning():self.worker.cancelled.set();self.message.setText('正在取消，请稍候…')
            else:self.close()
        def closeEvent(self,event):
            if self.worker and self.worker.isRunning():
                self.close_requested=True;self.worker.cancelled.set();self.message.setText('正在取消，下载结束后关闭…');event.ignore()
            else:event.accept()
    try:
        window=Window();window.show()
        if args.smoke_test:
            application.processEvents()
            assert all(label.text() and label.width()>0 and label.height()>0 for label in window.findChildren(QLabel))
            print('Compatibility updater GUI ready; no network request.',flush=True)
            print('Rendered font: '+application.font().family(),flush=True)
            window.grab().save(helper._native(root/'cache/updates/compatibility-preview.png'))
            QTimer.singleShot(250,application.quit)
        return application.exec_()
    finally:guard.unlock()


if __name__=='__main__':
    parser_root = next((sys.argv[i+1] for i,arg in enumerate(sys.argv[:-1]) if arg=='--root'),None)
    if parser_root:
        target=Path(parser_root).resolve()
        log=helper.validate_destination(target,'cache/updates/compatibility.log');log.parent.mkdir(parents=True,exist_ok=True)
        sys.stdout=sys.stderr=log.open('a',encoding='utf-8',buffering=1)
    try:raise SystemExit(main())
    except Exception:
        traceback.print_exc();raise SystemExit(1)
