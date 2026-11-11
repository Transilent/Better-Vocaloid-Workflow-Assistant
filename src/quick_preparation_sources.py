"""Source choices and review state shared across the Quick preparation page."""
from pathlib import Path

from PySide6.QtCore import QObject, Property, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

import bilibili_session
import lyrics_sources
import preparation_commands as preparation


class QuickPreparationSources(QObject):
    changed = Signal()

    def __init__(self, owner, worker):
        super().__init__(owner)
        self.owner, self.worker = owner, worker
        self._songs, self._downloads, self._issues = [], [], []
        self._reference = None
        self._reference_revision = None
        self._checked_bv = self._selected_job = self._selected_bv = ''
        self._message = ''
        self._login = ''
        self._network = {'use_login': False, 'timeout': 30, 'retries': 3}
        self._network_loaded = False
        worker.changed.connect(self.changed)
        owner.draftChanged.connect(self.changed)

    @property
    def draft(self):
        return self.owner.state.draft

    def reusable_job(self):
        if self._selected_job:
            try:
                if preparation.bilibili_draft_bv(self.draft) == self._selected_bv and self.draft.voice_mode != 'import':
                    return self._selected_job
            except ValueError:
                pass
        return ''

    @Property('QVariantMap', notify=changed)
    def view(self):
        reference = self._reference
        return {'busy': bool(self.worker.kind), 'songs': self._songs, 'downloads': self._downloads,
                'preview': '\n'.join(cue.text for cue in reference.cues) if reference else '',
                'previewId': reference.origin.get('song_id', '') if reference else '',
                'canApply': bool(reference and self._reference_revision == self.draft.revision),
                'selectedDownload': Path(self.reusable_job()).name if self.reusable_job() else '',
                'message': self._message, 'issues': self._issues, 'network': self._network,
                'networkLoaded': self._network_loaded, 'login': self._login}

    def _start(self, kind, label, work, accept):
        if self.worker.kind:
            return
        self._message = label

        def finish(value, error):
            if error is None:
                try:
                    accept(value)
                except (OSError, ValueError, TypeError, KeyError) as failure:
                    error = failure
            if error is not None:
                self._message = str(error)[:1000]
            self.changed.emit()

        self.worker.start('preparation-source-' + kind, work, finish)

    @Slot(str)
    def searchSongs(self, keyword):
        if self.worker.kind:
            return
        try:
            identifier = lyrics_sources.song_id(keyword)
        except ValueError:
            identifier = None
        if identifier:
            self.previewSong(identifier)
            return
        self._songs = []
        self._reference = None

        def accept(songs):
            self._songs = [{**song, 'label': f"{song['title']} · {song['artists']} · {song['album']} · "
                                            f"{int(song['duration']) // 60}:{int(song['duration']) % 60:02d}"}
                           for song in songs]
            self._message = f'找到 {len(songs)} 个版本，请先选择并核对。'

        self._start('search', '正在查找歌曲版本…', lambda _stop, _send: lyrics_sources.search(keyword), accept)

    @Slot(str)
    def previewSong(self, song):
        if self.worker.kind:
            return
        self._reference = None
        revision = self.draft.revision

        def accept(reference):
            self._reference, self._reference_revision = reference, revision
            self._message = f'已读取 {len(reference.cues)} 行歌词。确认版本后再填入草稿。'

        self._start('lyrics', '正在读取原文歌词…', lambda _stop, _send: lyrics_sources.fetch(song), accept)

    @Slot(str)
    def auditionSong(self, song):
        try:
            identity = lyrics_sources.song_id(song)
            if not QDesktopServices.openUrl(QUrl('https://music.163.com/song?id=' + identity)):
                raise ValueError('无法打开歌曲网页。')
        except (OSError, ValueError) as error:
            self._message = str(error)
            self.changed.emit()

    @Slot(result=bool)
    def applyLyrics(self):
        if self.worker.kind or not self.view['canApply']:
            return False
        draft = self.draft.apply_lyrics('lead', self._reference).edit('reference_mode', 'netease')
        self.owner._update_draft(lambda: self.owner.state.set_draft(draft))
        self._reference = None
        self.changed.emit()
        return True

    @Slot()
    def checkComponents(self):
        draft = self.draft

        def accept(issues):
            if self.draft.revision != draft.revision:
                raise ValueError('准备选择已变化，请重新检查。')
            self._issues = issues
            self._message = '请处理当前选择的问题后继续。' if issues else '当前所选的可选组件检查通过。'

        self._issues = []
        self._start('check', '正在检查所选组件…',
                    lambda _stop, _send: preparation.selected_component_issues(draft), accept)

    @Slot()
    def refreshDownloadSettings(self):
        def work(_stop, _send):
            values = bilibili_session.options()
            if not values['use_login']:
                return values, '当前不使用下载登录。'
            try:
                available = any(row.get('name') == 'SESSDATA' and row.get('value')
                                for row in bilibili_session.read_cookies())
            except (ImportError, OSError, ValueError):
                available = False
            return values, ('本机登录可读取；下载时再核验在线状态。' if available else
                            '本机没有可用的下载登录。请登录，或关闭登录选项。')

        def accept(value):
            self._network, self._login = value
            self._network_loaded = True
            self._message = ''

        self._start('network', '正在读取本机下载设置…', work, accept)

    @Slot(bool, int, int)
    def saveDownloadSettings(self, use_login, timeout, retries):
        if self.worker.kind or not self._network_loaded:
            return
        try:
            bilibili_session.save_options(use_login=use_login, timeout=timeout, retries=retries)
        except (OSError, ValueError) as error:
            self._message = str(error)
            self.changed.emit()
            return
        self.refreshDownloadSettings()

    @Slot()
    def loginDownload(self):
        if not self.worker.kind:
            if self.owner.publication.loginBilibili():
                self._message = '请在专用窗口登录，关闭后刷新本机登录状态。'
            else:
                self._message = self.owner.publication.view.get('message') or '未能打开登录会话。'
            self.changed.emit()

    @Slot()
    def findDownloads(self):
        if self.worker.kind:
            return
        self._downloads = []
        try:
            bv = preparation.bilibili_draft_bv(self.draft)
            if self.draft.voice_mode == 'import':
                raise ValueError('导入分轨不需要下载原素材。')
        except ValueError as error:
            self._message = str(error)
            self.changed.emit()
            return

        def work(stop, _send):
            from pipeline import verified_download_reuse
            from task_store import records

            matches = []
            for row in records(check=stop.is_set):
                if stop.is_set():
                    raise InterruptedError('已停止检查。')
                if row.get('request', {}).get('bv') == bv and verified_download_reuse(row['path'], bv):
                    matches.append({'job': str(row['path']), 'label': str(row['title']), 'bv': bv})
                    if len(matches) >= 20:
                        break
            return matches

        def accept(matches):
            if preparation.bilibili_draft_bv(self.draft) != bv:
                raise ValueError('来源已变化，请重新查找。')
            self._checked_bv = bv
            self._downloads = matches
            self._message = f'找到 {len(matches)} 个下载回执完整的同源任务。'

        self._start('download-reuse', '正在核验同一 BV 的下载文件…', work, accept)

    @Slot(str, result=bool)
    def selectDownload(self, job):
        if not self.worker.kind and not job:
            self._selected_job = self._selected_bv = ''
        elif not self.worker.kind and any(row['job'] == job for row in self._downloads):
            try:
                if preparation.bilibili_draft_bv(self.draft) != self._checked_bv:
                    return False
            except ValueError:
                return False
            self._selected_job, self._selected_bv = job, self._checked_bv
        else:
            return False
        self.changed.emit()
        return True
