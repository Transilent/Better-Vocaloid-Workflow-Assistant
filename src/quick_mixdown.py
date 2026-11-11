"""Resident Quick mixdown controller.

The QML page is a projection of this object.  One native realtime player lives
for the lifetime of the workspace, source and parameter edits are staged
separately from export, and export registration is an explicit transaction
against an already frozen render request.
"""
from __future__ import annotations

from datetime import datetime
import copy
import json
from pathlib import Path
import shutil
import threading
import time
import uuid

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

import mixdown_export
from mixdown_contract import MAX_VOCALS
from mixdown_limiter_spec import PEAK_CEILING_DBFS
from mixdown_preview_controller import MixdownPreviewController
from mixdown_runtime import (
    MixdownCancelled,
    activate_pedalboard,
    cached_optional_packages,
    child_python,
    component_manifest,
    file_sha256,
    normal_path_key,
    optional_packages,
    run_component_installer,
    worker_environment,
)
from mixdown_workspace_state import (
    MixdownWorkspaceState,
    eq_curve,
    meters_from_telemetry,
    reverb_curve_from_telemetry,
    spectrum_from_telemetry,
    telemetry_identity,
)
from preset_library import PresetLibrary
from common import ROOT
from quick_work import WorkerLane


_PLAYER_SIGNALS = (
    'positionChanged', 'durationChanged', 'stateChanged', 'mediaStatusChanged',
    'sourceChanged', 'errorOccurred', 'prepared', 'parametersApplied',
    'seekConfirmed', 'telemetryChanged',
)


def _local_url(value: Any) -> Path:
    if isinstance(value, QUrl):
        if not value.isLocalFile():
            raise ValueError('请选择本机文件。')
        text = value.toLocalFile()
    else:
        text = str(value or '')
    if not text.strip():
        raise ValueError('文件路径为空。')
    return Path(text).expanduser().resolve()


def _artifacts_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict) and isinstance(value.get('artifacts'), list):
        value = value['artifacts']
    if not isinstance(value, (list, tuple)):
        raise ValueError('任务分轨必须来自 quick_results 的 artifacts 列表。')
    rows = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError('任务产物条目无效。')
        rows.append(dict(item))
    return rows


class QuickMixdown(QObject):
    viewChanged = Signal()
    positionChanged = Signal()
    tracksChanged = Signal()
    trackIdsChanged = Signal()
    bandsChanged = Signal()
    eqCurveChanged = Signal()
    metersChanged = Signal()
    spectrumChanged = Signal()
    reverbCurveChanged = Signal()
    presetsChanged = Signal()

    def __init__(self, parent, worker):
        super().__init__(parent)
        self._worker = worker
        self._closed = False
        self._initialized = False
        self._lock = threading.RLock()
        self._active_kind = ''
        self._support_lane = WorkerLane(self, f'vwa-mixdown-support-{id(self):x}')
        self._state = MixdownWorkspaceState()
        self._library = PresetLibrary()
        self._presets = []
        self._preset_meta = {}
        self._view = {}
        self._tracks = self._state.tracks
        self._bands = self._state.bands
        self._eq_basis = (self._state.eq_enabled, self._state.sample_rate)
        self._eq_curve = []
        self._meters = {
            'lanes': [{'id': row['id'], 'rms': None, 'peak': None} for row in self._tracks],
            'gr': None, 'limiter': None, 'tail': None, 'source': '',
        }
        self._meters['lanes'].append({'id': 'master', 'rms': None, 'peak': None})
        self._spectrum = {'before': [], 'after': [], 'freqs': [], 'source': ''}
        self._reverb_curve = []
        self._support_ready = False
        self._support = {
            'visible': False, 'ready': False, 'busy': False, 'canInstall': False,
            'state': 'idle', 'message': '进入混音页后将在本地检查混音组件。',
            'detail': '', 'installerAvailable': False, 'playerAvailable': False,
            'installLabel': '安装混音组件',
        }
        self._export_plan = None
        self._export_receipt = None
        self._export_task_artifacts = []
        self._result_fingerprint = ''
        self._preview_pending = False
        self._last_telemetry_identity = None
        self._last_spectrum_at = 0.0
        self._last_meter_at = 0.0
        self._last_reverb_key = None
        self._last_reverb_at = 0.0
        self._resume_position_ms = 0
        self._resume_play_intent = False
        self._cache_dir = None
        self._player = None
        self._preview = None
        self._native_dll = None
        self._component_paths = None

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(90)
        self._preview_timer.timeout.connect(self._refresh_preview)

        self._eq_timer = QTimer(self)
        self._eq_timer.setSingleShot(True)
        self._eq_timer.setInterval(50)
        self._eq_timer.timeout.connect(self._emit_eq_curve)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(33)
        self._poll_timer.timeout.connect(self._poll_player)

        self._freshness_timer = QTimer(self)
        self._freshness_timer.setInterval(1000)
        self._freshness_timer.timeout.connect(self._refresh_freshness)

        self._support_retry = QTimer(self)
        self._support_retry.setSingleShot(True)
        self._support_retry.setInterval(650)
        self._support_retry.timeout.connect(self._start_support_check)

        self._worker.changed.connect(self._worker_changed)
        self._support_lane.changed.connect(self._worker_changed)
        self._refresh_component_paths()
        self._load_presets()
        self._emit_eq_curve()
        with self._lock:
            self._view = self._recompute_view_locked()
            self._tracks = self._state.tracks
            self._bands = self._state.bands

    @Slot()
    def enter(self):
        """Initialize the resident session only on an explicit page/task entry."""
        if self._closed:
            return False
        if self._initialized:
            if not self._support_ready and not self._support_lane.kind:
                self._start_support_check()
            return True
        self._initialized = True
        cache_root = (ROOT / 'cache' / 'mixdown-preview').resolve()
        candidate = (cache_root / ('quick-' + uuid.uuid4().hex)).resolve()
        if not candidate.is_relative_to(cache_root):
            raise ValueError('混音缓存路径越界。')
        candidate.mkdir(parents=True, exist_ok=True)
        self._cache_dir = candidate
        self._refresh_component_paths()
        self._create_player()
        self._poll_timer.start()
        self._freshness_timer.start()
        self._recompute_and_emit()
        self._start_support_check()
        return True

    def _ensure_entered(self):
        if not self._initialized:
            return self.enter()
        return not self._closed

    def _discard_cache_dir(self):
        cache = self._cache_dir
        if not cache:
            return
        try:
            candidate = Path(cache).resolve()
            root = (ROOT / 'cache' / 'mixdown-preview').resolve()
            if candidate.is_relative_to(root) and not candidate.is_symlink():
                shutil.rmtree(candidate, ignore_errors=True)
        except OSError:
            pass

    @Property('QVariantMap', notify=viewChanged)
    def view(self):
        return copy.deepcopy(self._view)

    @Property(int, notify=positionChanged)
    def position(self):
        return self._state.position

    @Property('QVariantList', notify=tracksChanged)
    def tracks(self):
        return copy.deepcopy(self._tracks)

    @Property('QStringList', notify=trackIdsChanged)
    def trackIds(self):
        return [row['id'] for row in self._tracks]

    @Property('QVariantList', notify=bandsChanged)
    def bands(self):
        return copy.deepcopy(self._bands)

    @Property('QVariantList', notify=eqCurveChanged)
    def eqCurve(self):
        return copy.deepcopy(self._eq_curve)

    @Property('QVariantMap', notify=metersChanged)
    def meters(self):
        return copy.deepcopy(self._meters)

    @Property('QVariantMap', notify=spectrumChanged)
    def spectrum(self):
        return copy.deepcopy(self._spectrum)

    @Property('QVariantList', notify=reverbCurveChanged)
    def reverbCurve(self):
        return copy.deepcopy(self._reverb_curve)

    @Property('QVariantList', notify=presetsChanged)
    def presets(self):
        return copy.deepcopy(self._presets)

    @Property('QVariantMap', notify=viewChanged)
    def support(self):
        return copy.deepcopy(self._support)

    @Property(bool, notify=viewChanged)
    def supportVisible(self):
        return bool(self._support.get('visible'))

    @staticmethod
    def _monitor_volume(gain_db: Any) -> float:
        gain = max(-60.0, min(0.0, float(gain_db)))
        return 0.0 if gain <= -60.0 else min(100.0, 100.0 * (10.0 ** (gain / 20.0)))

    def _notice(self, message: Any) -> None:
        with self._lock:
            self._state.message = str(message or '')[:1600]
        self._recompute_and_emit()

    def _refresh_component_paths(self):
        try:
            root, spec, installer = component_manifest()
            packages = optional_packages(root, spec) if root and spec else None
            self._component_paths = (root, spec, installer, packages)
            available = bool(installer and Path(installer).is_file())
            label = '安装混音组件'
            try:
                size = float(spec['wheel']['bytes']) / 1024.0 / 1024.0
                label += '（约 {0:.1f} MiB）'.format(size)
            except (KeyError, TypeError, ValueError):
                pass
            self._support.update(installerAvailable=available, installLabel=label)
            return self._component_paths
        except Exception as error:
            self._component_paths = (None, None, None, None)
            self._support.update(installerAvailable=False,
                                 message='混音组件探测失败：' + str(error))
            return self._component_paths

    def _create_player(self):
        try:
            from quick_mixdown_player import QuickMixdownPlayer, native_output_path
            self._native_dll = native_output_path()
            if self._cache_dir is None:
                cache_root = (ROOT / 'cache' / 'mixdown-preview').resolve()
                self._cache_dir = (cache_root / ('quick-' + uuid.uuid4().hex)).resolve()
                self._cache_dir.mkdir(parents=True, exist_ok=True)
            root, spec, installer, packages = self._component_paths or (None, None, None, None)
            selected = packages or cached_optional_packages()
            options = {
                'python_executable': child_python(),
                'extra_paths': [selected] if selected else [],
                'environment': worker_environment(selected),
                'startup_timeout': 8.0,
                'shutdown_timeout': 2.0,
            }
            self._player = QuickMixdownPlayer(
                self._native_dll, cache_dir=self._cache_dir, parent=self,
                service_options=options,
            )
            self._player.available = bool(self._native_dll)
            self._preview = MixdownPreviewController(self._player)
            for name in _PLAYER_SIGNALS:
                signal = getattr(self._player, name, None)
                if signal is None:
                    continue
                slot = {
                    'errorOccurred': self._player_error,
                    'prepared': self._player_prepared,
                    'parametersApplied': self._parameters_applied,
                    'telemetryChanged': self._telemetry,
                }.get(name)
                if slot is not None:
                    signal.connect(slot)
            self._player.set_volume(self._monitor_volume(self._state.master_gain))
            self._support['playerAvailable'] = bool(self._native_dll)
            if not self._native_dll:
                self._state.message = '连续试听原生输出组件缺失；仍可检查组件并导出 WAV。'
        except Exception as error:
            self._player = None
            self._preview = None
            self._support['playerAvailable'] = False
            self._state.message = '连续试听初始化失败：' + str(error)

    def _replace_player(self):
        old = self._player
        if self._preview is not None:
            self._resume_position_ms = int(self._preview.position_ms)
            self._resume_play_intent = bool(self._preview.play_intent)
            try:
                self._preview.release()
            except Exception:
                pass
        self._player = self._preview = None
        if old is not None:
            try:
                old.close(timeout=2.0)
            except Exception:
                pass
        self._create_player()
        if self._player is not None and self._resume_play_intent:
            self._player.set_play_intent(True)
        self._clear_hotspots()
        if self._state.has_media():
            self._schedule_preview(0)
        self._recompute_and_emit()

    def _worker_changed(self):
        self._recompute_and_emit()

    def _start_owned(self, kind, work, finish, progress=None):
        with self._lock:
            if self._closed:
                return False
            if self._active_kind:
                return False
            self._active_kind = kind
            self._state.busy = True
        started = self._worker.start(
            kind,
            work,
            lambda value, error, kind=kind, finish=finish: self._finish_owned(
                kind, value, error, finish),
            progress=progress,
        )
        if not started:
            with self._lock:
                self._active_kind = ''
                self._state.busy = False
            self._recompute_and_emit()
            return False
        self._recompute_and_emit()
        return True

    def _finish_owned(self, kind, value, error, finish):
        try:
            if not self._closed:
                finish(value, error)
        finally:
            with self._lock:
                if self._active_kind == kind:
                    self._active_kind = ''
                self._state.busy = bool(self._active_kind)
                self._state.loading = bool(self._player and self._player.loading)
            self._recompute_and_emit()

    def _start_support_check(self):
        if not self._initialized or self._closed or self._support_ready:
            return
        if self._support_lane.kind:
            return
        root, spec, installer, packages = self._refresh_component_paths()
        self._support.update(visible=True, ready=False, busy=True, canInstall=False,
                             state='checking', message='正在本地检查 Pedalboard…',
                             detail='')
        self._recompute_and_emit()
        if root is None or spec is None:
            self._apply_support_result(False, '当前版本未包含 Pedalboard 组件清单或离线安装器。',
                                       packages, installed=False)
            return

        def work(stop, _send):
            ready, detail = activate_pedalboard(packages, check=stop.is_set)
            return {'ready': bool(ready), 'detail': str(detail or ''),
                    'packages': str(packages or ''), 'installed': False}

        def finish(value, error):
            try:
                if not self._closed:
                    self._support_finish(value, error, False)
            finally:
                self._recompute_and_emit()

        if not self._support_lane.start('mixdown-support-probe', work, finish):
            self._support_retry.start()

    def _support_finish(self, value, error, installed):
        if isinstance(error, (InterruptedError, MixdownCancelled)):
            self._apply_support_result(False, '混音组件检查已取消。', None, installed=False)
            return
        if error is not None:
            self._apply_support_result(False, str(error), None, installed=False)
            return
        if not isinstance(value, dict):
            self._apply_support_result(False, '混音组件检查返回无效结果。', None, installed)
            return
        self._refresh_component_paths()
        self._apply_support_result(
            bool(value.get('ready')), str(value.get('detail') or ''),
            value.get('packages') or None, installed=bool(installed))

    def _apply_support_result(self, ready, detail, packages, *, installed):
        self._support_ready = bool(ready)
        self._support.update(
            ready=bool(ready), busy=False, visible=not bool(ready),
            canInstall=bool(not ready and self._support.get('installerAvailable')),
            state='ready' if ready else ('installed_unverified' if installed else 'missing'),
            message=('混音组件已就绪。' if ready else
                     ('组件安装完成，但本地验签/导入仍未通过：' + detail if installed
                      else '缺少或无法使用 Pedalboard：' + (detail or '本地组件未安装。'))),
            detail=str(detail or ''),
        )
        with self._lock:
            self._state.message = self._support['message']
        if ready and installed:
            self._replace_player()
        self._recompute_and_emit()

    def _clear_hotspots(self):
        with self._lock:
            counters = []
            for row in self._tracks:
                counters.append({'id': row['id'], 'rms': None, 'peak': None})
            counters.append({'id': 'master', 'rms': None, 'peak': None})
            self._meters = {'lanes': counters, 'gr': None, 'limiter': None,
                            'tail': None, 'source': ''}
            self._spectrum = {'before': [], 'after': [], 'freqs': [], 'source': ''}
            self._reverb_curve = []
            self._last_telemetry_identity = None
        self.metersChanged.emit()
        self.spectrumChanged.emit()
        self.reverbCurveChanged.emit()

    def _recompute_view_locked(self):
        player = self._player
        if self._preview is not None and player is not None:
            self._state.set_duration(player.duration())
            self._state.playing = bool(player.play_intent and not player.failed)
            self._state.loading = bool(player.loading)
        can_play = bool(self._support_ready and player is not None and player.available
                        and self._state.has_media())
        try:
            self._state.validate_output()
            inputs_ready = True
        except (OSError, ValueError, TypeError):
            inputs_ready = False
        support_probe_busy = bool(self._support_lane.kind)
        can_export = bool(self._support_ready and inputs_ready and not self._active_kind
                          and not support_probe_busy)
        can_register = bool(self._state.job and self._state.result and not self._state.result_stale
                            and self._export_receipt and not self._active_kind
                            and not support_probe_busy)
        if self._active_kind == 'mixdown-export':
            state_name = 'exporting'
        elif self._active_kind == 'mixdown-register':
            state_name = 'registering'
        elif support_probe_busy or self._active_kind == 'mixdown-install':
            state_name = 'checking'
        elif player is not None and player.failed:
            state_name = 'error'
        elif self._state.loading:
            state_name = 'loading'
        elif self._state.playing:
            state_name = 'playing'
        elif self._state.result:
            state_name = 'result'
        elif self._state.has_media():
            state_name = 'ready'
        else:
            state_name = 'idle'
        self._state.state = state_name
        self._state.busy = bool(self._active_kind or self._state.loading or support_probe_busy)
        self._support['busy'] = bool(support_probe_busy or self._active_kind == 'mixdown-install')
        self._support['playerAvailable'] = bool(player is not None and player.available)
        self._support['visible'] = bool(
            self._initialized and (not self._support_ready or not self._support['playerAvailable']))
        self._support['canInstall'] = bool(
            self._initialized and not self._support_ready
            and self._support.get('installerAvailable')
            and not self._active_kind and not support_probe_busy)
        view = self._state.view(can_export=can_export, can_register=can_register,
                                can_play=can_play)
        view['support'] = copy.deepcopy(self._support)
        view['supportVisible'] = bool(self._support['visible'])
        view['componentMissing'] = bool(self._initialized and not self._support_ready)
        view['supportMessage'] = str(self._support.get('message') or '')
        view['nativeOutputAvailable'] = bool(player is not None and player.available)
        return view

    def _recompute_and_emit(self):
        self._sync_position()
        with self._lock:
            view = self._recompute_view_locked()
            changed = view != self._view
            tracks, bands = self._state.tracks, self._state.bands
            ids_changed = [row['id'] for row in tracks] != self.trackIds
            tracks_changed, bands_changed = tracks != self._tracks, bands != self._bands
            eq_basis = (self._state.eq_enabled, self._state.sample_rate)
            eq_changed = bands_changed or eq_basis != self._eq_basis
            self._view = view
            self._tracks, self._bands, self._eq_basis = tracks, bands, eq_basis
        if tracks_changed:
            self.tracksChanged.emit()
        if ids_changed:
            self.trackIdsChanged.emit()
        if bands_changed:
            self.bandsChanged.emit()
        if eq_changed:
            self._eq_timer.start()
        if changed:
            self.viewChanged.emit()

    def _sync_position(self):
        position = self._preview.position_ms if self._preview is not None else 0
        if position != self._state.position:
            self._state.set_position(position)
            self.positionChanged.emit()

    def _after_render_change(self, *, preset_name=None):
        if self._preview is not None:
            self._preview.dirty = True
        with self._lock:
            self._state.preset_name = '自定义' if preset_name is None else str(preset_name)
            if self._state.result:
                self._state.result_stale = self._state.render_fingerprint() != self._result_fingerprint
        self._schedule_preview()
        self._recompute_and_emit()

    def _refresh_freshness(self):
        with self._lock:
            if not self._state.result:
                return
            fresh = self._state.render_fingerprint() == self._result_fingerprint
            stale = not fresh
            changed = self._state.result_stale != stale
            if changed:
                self._state.result_stale = stale
        if changed:
            self._recompute_and_emit()

    def _emit_eq_curve(self):
        try:
            with self._lock:
                curve = eq_curve(self._state.bands if self._state.eq_enabled else (),
                                 self._state.sample_rate)
            if curve != self._eq_curve:
                self._eq_curve = curve
                self.eqCurveChanged.emit()
        except Exception as error:
            self._notice('EQ 曲线计算失败：' + str(error))

    def _schedule_preview(self, delay=90):
        if self._closed:
            return
        self._preview_timer.start(max(0, int(delay)))

    def _refresh_preview(self):
        if self._closed or self._preview is None or self._player is None:
            return False
        if not self._support_ready or not self._player.available:
            self._clear_hotspots()
            return False
        if self._player.failed and not self._preview.play_intent:
            return False
        try:
            with self._lock:
                if not self._state.has_media():
                    raise ValueError('请选择歌声与伴奏。')
                bypass = self._state.selection == 'A'
                request = self._state.request(
                    Path(self._cache_dir) / 'live-unused.wav',
                    bypass_processing=bypass)
                source_key = self._state.source_key()
            duration = self._player.duration()
            loop_range = (0, duration) if self._state.loop else (0, 0)
            self._preview_pending = True
            start_ms = self._resume_position_ms or self._preview.position_ms
            accepted = self._preview.refresh(
                request, source_key, start_ms=start_ms,
                loop_range=loop_range)
            if not accepted:
                self._preview_pending = False
                return False
            self._resume_position_ms = 0
            self._clear_hotspots()
            if not self._player.ready:
                with self._lock:
                    if not self._active_kind:
                        self._state.message = '正在从常驻进程准备连续试听…'
                self._recompute_and_emit()
            return True
        except (OSError, ValueError, TypeError) as error:
            self._preview_pending = False
            self._clear_hotspots()
            with self._lock:
                self._state.message = str(error)
            self._recompute_and_emit()
            return False

    def _player_prepared(self):
        self._preview_pending = False
        with self._lock:
            if self._state.message == '正在从常驻进程准备连续试听…':
                self._state.message = '连续试听已就绪；可切换 A/B 并试听整曲。'
            if self._player is not None:
                self._state.set_duration(self._player.duration())
        self._recompute_and_emit()

    def _parameters_applied(self, key):
        if self._preview is not None and (self._preview.dirty or key != self._preview.requested_key):
            return
        self._preview_pending = False

    def _player_error(self, message):
        self._preview_pending = False
        if self._preview is not None:
            self._preview.failed()
        self._clear_hotspots()
        with self._lock:
            self._state.message = str(message)[:1600]
        self._recompute_and_emit()

    def _telemetry(self, snapshot):
        if not isinstance(snapshot, dict) or self._closed:
            return
        data = snapshot.get('telemetry')
        if not isinstance(data, dict):
            return
        # The renderer follows the accompaniment. Updates to the displayed
        # Nyquist limit never become edits or retire a transport generation.
        if self._preview is not None and not self._preview.dirty:
            rate = data.get('sample_rate')
            if isinstance(rate, (int, float)) and 8000 <= rate <= 384000:
                with self._lock:
                    rate_changed = self._state.set_sample_rate(rate)
                if rate_changed:
                    self._recompute_and_emit()
        if data.get('status') != 'ready':
            self._clear_hotspots()
            return
        if self._preview_pending or (self._preview is not None and self._preview.dirty):
            return
        try:
            identity = telemetry_identity(data, snapshot)
        except (TypeError, ValueError):
            return
        if identity == self._last_telemetry_identity:
            return
        self._last_telemetry_identity = identity
        media_frame, revision, epoch, telemetry_source = identity
        with self._lock:
            vocal_ids = [row['id'] for row in self._state.active_vocals()]
        now = time.monotonic()
        if now - self._last_meter_at >= 0.066:
            self._last_meter_at = now
            self._meters = meters_from_telemetry(data, vocal_ids)
            self.metersChanged.emit()
        if now - self._last_spectrum_at >= 0.10:
            self._last_spectrum_at = now
            spectrum = spectrum_from_telemetry(data)
            if spectrum != self._spectrum:
                self._spectrum = spectrum
                self.spectrumChanged.emit()
        reverb = data.get('reverb') if isinstance(data.get('reverb'), dict) else {}
        kernel = reverb.get('kernel_edc') if isinstance(reverb.get('kernel_edc'), dict) else {}
        key = (kernel.get('key'), kernel.get('signature'), self._player.generation if self._player else None,
               revision, epoch)
        if (self._state.reverb_enabled and kernel.get('available') is True
                and kernel.get('measured') is True and key != self._last_reverb_key
                and now - self._last_reverb_at >= 0.5):
            curve = reverb_curve_from_telemetry(data)
            self._last_reverb_key = key
            self._last_reverb_at = now
            if curve != self._reverb_curve:
                self._reverb_curve = curve
                self.reverbCurveChanged.emit()
        elif not self._state.reverb_enabled and self._reverb_curve:
            self._reverb_curve = []
            self._last_reverb_key = None
            self.reverbCurveChanged.emit()

    def _poll_player(self):
        if self._closed or self._player is None:
            return
        try:
            self._player.poll()
            if self._preview is not None and self._player.ready:
                self._preview.follow_position()
            self._sync_position()
        except Exception as error:
            self._player_error(str(error))
            return
        if (self._state.duration != self._player.duration()
                or self._state.playing != bool(self._player.play_intent and not self._player.failed)
                or self._state.loading != bool(self._player.loading)):
            self._recompute_and_emit()

    @Slot(str, QUrl)
    def chooseSource(self, track_id, url):
        if not self._ensure_entered():
            return
        try:
            path = _local_url(url)
            with self._lock:
                if self._active_kind in ('mixdown-register',):
                    raise ValueError('正在写入任务登记，暂时不能更换输入。')
                self._state.set_source(track_id, path)
                self._state.message = '已选择本地音轨；文件保持原位且只读。'
            self._after_render_change()
        except Exception as error:
            self._notice(error)

    @Slot(str)
    def clearSource(self, track_id):
        if not self._ensure_entered():
            return
        try:
            with self._lock:
                self._state.clear_source(track_id)
                self._state.message = '已移除该路音轨，不会删除原文件。'
            if self._preview is not None:
                self._preview.release()
            self._clear_hotspots()
            self._after_render_change()
        except Exception as error:
            self._notice(error)

    @Slot()
    def addTrack(self):
        if not self._ensure_entered():
            return
        try:
            with self._lock:
                identifier = self._state.add_vocal()
                self._state.message = '已增加一路空歌声轨；选择 WAV 后才参与混音。'
            self._recompute_and_emit()
            return identifier
        except Exception as error:
            self._notice(error)
            return ''

    @Slot(str)
    def removeTrack(self, track_id):
        if not self._ensure_entered():
            return
        try:
            with self._lock:
                old_has_media = self._state.has_media()
                self._state.remove_vocal(track_id)
                self._state.message = '已移除该歌声通道。'
            if self._preview is not None and old_has_media and not self._state.has_media():
                self._preview.release()
                self._clear_hotspots()
            self._after_render_change()
        except Exception as error:
            self._notice(error)

    @Slot(str, str, 'QVariant')
    def setTrack(self, track_id, key, value):
        if not self._ensure_entered():
            return
        try:
            with self._lock:
                self._state.set_track(track_id, key, value)
                self._state.message = '通道参数已更新。'
            self._after_render_change()
        except Exception as error:
            self._notice(error)

    @Slot(str, 'QVariant')
    def setOption(self, name, value):
        if not self._ensure_entered():
            return
        try:
            folded_name = str(name).replace('_', '').replace('-', '').casefold()
            with self._lock:
                changed, label = self._state.set_option(name, value)
                if folded_name in ('mastergain', 'volume'):
                    if self._player is not None:
                        self._player.set_volume(self._monitor_volume(self._state.master_gain))
                    self._state.message = '{0}已更新为 {1:g} dB；该值用于试听输出。'.format(
                        label, self._state.master_gain)
                    self._recompute_and_emit()
                    return
                if folded_name in ('limiterenabled', 'limiterceiling'):
                    self._state.message = '最终安全限制器固定为 {0:g} dBFS。'.format(
                        float(PEAK_CEILING_DBFS))
                    self._recompute_and_emit()
                    return
                self._state.message = '{0}已更新。'.format(label)
            if changed:
                self._after_render_change()
            else:
                self._recompute_and_emit()
        except Exception as error:
            self._notice(error)

    @Slot(int, str, 'QVariant')
    def setBand(self, index, key, value):
        if not self._ensure_entered():
            return
        try:
            with self._lock:
                self._state.set_band(index, key, value)
                self._state.message = 'EQ 参数已更新。'
            self._after_render_change()
        except Exception as error:
            self._notice(error)

    @Slot(int, float, float)
    def moveBand(self, index, frequency_hz, gain_db):
        if not self._ensure_entered():
            return
        try:
            with self._lock:
                self._state.move_band(index, frequency_hz, gain_db)
                self._state.message = 'EQ 控制点已移动。'
            self._after_render_change()
        except Exception as error:
            self._notice(error)

    @Slot(str)
    def setAnalysis(self, value):
        try:
            with self._lock:
                self._state.set_analysis(value)
            self._recompute_and_emit()
        except Exception as error:
            self._notice(error)

    @Slot(bool)
    def setExpanded(self, value):
        with self._lock:
            self._state.set_expanded(value)
        self._recompute_and_emit()

    @Slot(str)
    def selectMix(self, value):
        if not self._ensure_entered():
            return
        try:
            with self._lock:
                self._state.set_selection(value)
                selection = self._state.selection
            if self._preview is not None:
                self._preview.select('before' if selection == 'A' else 'after')
            self._schedule_preview(0)
            self._recompute_and_emit()
        except Exception as error:
            self._notice(error)

    @Slot()
    def togglePlay(self):
        if not self._ensure_entered():
            return
        if self._preview is None or self._player is None or not self._support_ready:
            self._notice('连续试听组件尚未就绪。')
            return
        if not self._player.available:
            self._notice('连续试听原生输出组件缺失。')
            return
        try:
            with self._lock:
                valid = self._state.has_media()
            if not valid:
                raise ValueError('请先选择歌声和伴奏。')
            wanted = not self._preview.play_intent
            self._preview.set_playing(wanted)
            if wanted and (self._preview.dirty or not self._player.ready):
                self._schedule_preview(0)
            self._recompute_and_emit()
        except Exception as error:
            self._notice(error)

    @Slot()
    def beginSeek(self):
        if not self._ensure_entered():
            return
        if self._preview is not None:
            self._preview.begin_seek()

    @Slot(float)
    def commitSeek(self, position):
        if not self._ensure_entered():
            return
        try:
            if self._preview is None or self._player is None or not self._player.ready:
                raise ValueError('连续试听尚未就绪。')
            self._preview.commit_seek(position)
            self._recompute_and_emit()
        except Exception as error:
            self._preview_pending = False
            self._notice(error)

    @Slot()
    def cancelSeek(self):
        """Drop a local drag gesture without pausing or releasing the session."""
        if self._preview is None:
            return
        self._preview.dragging = False
        self._recompute_and_emit()

    @Slot(bool)
    def setLoop(self, enabled):
        if not self._ensure_entered():
            return
        with self._lock:
            self._state.set_loop(enabled)
            duration = self._player.duration() if self._player is not None else 0
        if self._player is not None:
            self._player.set_loop(bool(enabled))
            self._player.set_loop_range(0, duration)
        self._recompute_and_emit()

    @Slot(QUrl)
    def chooseOutput(self, url):
        if not self._ensure_entered():
            return
        try:
            path = _local_url(url)
            with self._lock:
                self._state.set_output(path)
                self._state.message = ('导出目标：' + str(path))
            self._recompute_and_emit()
        except Exception as error:
            self._notice(error)

    @staticmethod
    def _build_export_receipt(report, context, source_rows, task_artifacts):
        output = Path(report['output_path']).resolve()
        info = output.stat()
        parameters = dict(context.get('parameters') or {})
        parameters['output_path'] = str(output)
        inputs = dict(context.get('inputs') or {})
        hashes = report.get('source_sha256') or {}
        hashes = {normal_path_key(path): value for path, value in hashes.items()}

        def with_hash(entry):
            row = dict(entry)
            row['sha256'] = hashes.get(normal_path_key(row['path']))
            return row

        saved_inputs = {
            'vocals': [with_hash(entry) for entry in inputs.get('vocals', [])],
            'instrumental_path': with_hash(inputs['instrumental_path']),
        }
        provenance = [
            {'track_id': row['id'], 'path': row['path'],
             'artifact_id': row.get('artifact_id'),
             'artifact_revision': row.get('artifact_revision'),
             'provenance': row.get('provenance')}
            for row in source_rows
        ]
        dependencies = [
            {'id': str(row.get('id') or ''), 'revision': str(row.get('revision') or '')}
            for row in task_artifacts
            if row.get('id') and row.get('revision')
        ]
        return {
            'schema': 'vwa.audio_mixdown.receipt/v2',
            'created_at': datetime.now().astimezone().isoformat(timespec='seconds'),
            'backend': 'audio_mixdown.render',
            'registration': 'explicit_user_action',
            'source_job': context.get('source_job'),
            'dependencies': dependencies,
            'inputs': saved_inputs,
            'input_provenance': provenance,
            'parameters': parameters,
            'output': {'path': str(output), 'bytes': info.st_size,
                       'mtime_ns': info.st_mtime_ns,
                       'sha256': report.get('output_sha256')},
            'render_result': {
                'sample_rate': report.get('sample_rate'),
                'duration_seconds': report.get('duration_seconds'),
                'peak_dbfs': report.get('peak_dbfs'),
                'warnings': list(report.get('warnings') or []),
            },
        }

    @Slot()
    def exportMix(self):
        if not self._ensure_entered():
            return
        if self._active_kind:
            self._notice('已有混音后台操作正在运行。')
            return
        if not self._support_ready:
            self._notice('混音组件尚未就绪；请先在本页执行明确安装。')
            return
        try:
            with self._lock:
                output = self._state.validate_output()
                request = self._state.request(output, bypass_processing=False)
                fingerprint = self._state.render_fingerprint()
                source_rows = self._state.source_rows()
                task_artifacts = self._state.task_artifacts()
                source_job = self._state.job or None
            plan = mixdown_export.plan_export(request, source_job=source_job)
            context = plan.receipt_context()
        except Exception as error:
            self._notice('无法开始导出：' + str(error))
            return
        with self._lock:
            self._state.message = '正在后台导出新的 WAV；现有源文件不会被覆盖。'

        def work(stop, send):
            report = mixdown_export.execute(plan, stop.is_set, progress=send)
            if not report.get('output_sha256'):
                report['output_sha256'] = file_sha256(plan.destination, stop.is_set)
            return report

        def finish(report, error):
            if isinstance(error, (InterruptedError, MixdownCancelled)):
                self._notice('导出已取消；临时 WAV 已清理，目标文件未被写入。')
                return
            if error is not None:
                self._notice('导出失败：' + str(error))
                return
            try:
                receipt = self._build_export_receipt(report, context, source_rows, task_artifacts)
                with self._lock:
                    fresh = self._state.render_fingerprint() == fingerprint
                    self._export_plan, self._export_receipt, self._export_task_artifacts = plan, receipt, task_artifacts
                    self._result_fingerprint = fingerprint
                    self._state.result = str(Path(report['output_path']).resolve())
                    self._state.result_stale = not fresh or not plan.signatures_unchanged()
                    if self._state.result_stale:
                        self._state.message = '已保留导出 WAV；当前输入或参数已变化，请重新导出后登记。'
                    else:
                        self._state.message = ('WAV 已导出；检查后可人工登记为任务 Mixdown。'
                                               if source_job else
                                               'WAV 已导出；独立文件无需任务登记。')
                self._recompute_and_emit()
            except Exception as exc:
                self._notice('导出完成但结果回执无效：' + str(exc))
        if not self._start_owned('mixdown-export', work, finish):
            self._notice('工作通道正被其他后台操作占用，未开始导出。')
            return
        self._recompute_and_emit()

    def _registration_token(self):
        with self._lock:
            return (
                self._state.job,
                self._state.render_fingerprint(),
                self._state.task_artifacts(),
                self._state.result,
            )

    @staticmethod
    def _artifact_identities(rows):
        return sorted((str(row.get('id') or ''), str(row.get('revision') or ''),
                       str(row.get('sha256') or '')) for row in rows)

    @Slot()
    def registerMix(self):
        if not self._ensure_entered():
            return
        if self._active_kind:
            self._notice('已有混音后台操作正在运行。')
            return
        with self._lock:
            job = self._state.job
            result = self._state.result
            stale = self._state.result_stale
            receipt = copy.deepcopy(self._export_receipt)
            task_artifacts = copy.deepcopy(self._export_task_artifacts)
        if not job:
            self._notice('当前为独立文件模式；请先从已核验任务载入分轨再登记。')
            return
        if not result or not receipt:
            self._notice('请先安全导出 WAV，再显式登记。')
            return
        if stale or not Path(result).is_file():
            self._notice('导出后输入、参数或文件已变化，旧结果不能登记。')
            return
        try:
            from task_store import locked
            if locked(job):
                raise ValueError('该任务仍被处理占用，请结束后再登记。')
            destination = Path(result).resolve()
            expected_sha = (receipt.get('output') or {}).get('sha256')
            if not expected_sha:
                raise ValueError('导出结果缺少文件 SHA-256，不能人工登记。')
            expected_token = self._registration_token()
            expected_artifacts = self._artifact_identities(task_artifacts)
        except Exception as error:
            self._notice('暂时不能登记：' + str(error))
            return

        def work(stop, _send):
            from task_store import locked
            from task_contract import bind, inspect, update_metadata
            if locked(job):
                raise ValueError('任务在登记开始前进入运行状态；本次没有写入任务登记。')
            if self._registration_token() != expected_token:
                raise ValueError('登记期间混音输入或参数已变化；本次没有写入任务登记。')
            if not self._export_plan.signatures_unchanged():
                raise ValueError('登记期间源音频已变化；本次没有写入任务登记。')
            if file_sha256(destination, stop.is_set) != expected_sha:
                raise ValueError('导出 WAV 在登记前发生变化；请重新导出后再登记。')
            if expected_artifacts:
                rows = inspect(job, check=stop.is_set)
                by_id = {str(row.get('id') or ''): row for row in rows}
                for identifier, revision, digest in expected_artifacts:
                    row = by_id.get(identifier)
                    if (row is None or str(row.get('revision') or '') != revision
                            or str(row.get('sha256') or '') != digest
                            or row.get('state') != 'verified'):
                        raise ValueError('任务分轨已变化或校验失效；本次没有写入任务登记。')
            if self._registration_token() != expected_token:
                raise ValueError('登记写入前混音输入或参数已变化；本次没有写入任务登记。')
            if locked(job):
                raise ValueError('任务在登记写入前进入运行状态；本次没有写入任务登记。')
            dependencies = [{'id': identifier, 'revision': revision}
                            for identifier, revision, _digest in expected_artifacts]
            artifact_id = bind(job, 'mixdown', destination,
                               dependencies=dependencies,
                               expected_sha256=expected_sha)
            saved = copy.deepcopy(receipt)
            saved['artifact_id'] = artifact_id
            saved['registered_at'] = datetime.now().astimezone().isoformat(timespec='seconds')
            update_metadata(job, audio_mixdown=saved)
            return str(destination)

        def finish(value, error):
            if isinstance(error, (InterruptedError, MixdownCancelled)):
                self._notice('登记已取消；没有写入任务。')
                return
            if error is not None:
                self._notice('登记失败：' + str(error))
                return
            with self._lock:
                self._state.message = '已人工登记为任务 Mixdown：' + str(value)
                self._state.result_stale = False
            self._recompute_and_emit()

        if not self._start_owned('mixdown-register', work, finish):
            self._notice('工作通道正被其他后台操作占用，未开始登记。')

    @Slot()
    def cancel(self):
        if self._support_lane.kind:
            self._support_lane.cancel()
            self._notice('正在取消本地组件检查…')
            return
        if not self._active_kind:
            return
        if self._active_kind == 'mixdown-register':
            self._notice('正在写入任务登记，需等待文件检查完成。')
            return
        self._worker.cancel()
        labels = {
            'mixdown-export': '正在取消导出并清理临时 WAV…',
            'mixdown-install': '正在取消组件安装…',
        }
        self._notice(labels.get(self._active_kind, '正在取消后台操作…'))

    def _resolve_preset(self, key):
        wanted = str(key or '').strip()
        if not wanted or wanted == 'custom':
            raise ValueError('请选择一个非自定义预设。')
        meta = self._preset_meta.get(wanted)
        if meta is None:
            raise ValueError('预设不在当前统一库快照中。')
        snapshot = self._library.get_snapshot(meta['id'], meta['version'])
        return snapshot['document'], snapshot['entry']

    @Slot()
    def refresh_presets(self):
        self._load_presets()
        self.presetsChanged.emit()
        return True

    def _load_presets(self):
        custom = {'key': 'custom', 'label': '自定义', 'value': 'custom'}
        try:
            snapshot = self._library.snapshot()
            rows = [custom]
            meta = {}
            labels = {}
            for entry in snapshot.get('entries', []):
                if entry.get('type') != 'mixdown':
                    continue
                identifier = str(entry.get('id') or '')
                version = str(entry.get('version') or '')
                if not identifier or not version:
                    continue
                if identifier.startswith('builtin.mixdown.'):
                    key = identifier[len('builtin.mixdown.'):]
                else:
                    key = identifier + '@' + version
                label = str(entry.get('name') or identifier)
                labels[label] = labels.get(label, 0) + 1
                if labels[label] > 1:
                    label += ' · 本机'
                rows.append({'key': key, 'label': label, 'value': key})
                meta[key] = {'id': identifier, 'version': version,
                             'sha256': entry.get('sha256'), 'label': label}
            self._presets = rows
            self._preset_meta = meta
        except Exception as error:
            self._presets = [custom]
            self._preset_meta = {}
            self._notice('预设库读取失败：' + str(error))

    @Slot(str)
    def applyPreset(self, key):
        if not self._ensure_entered():
            return
        if self._active_kind:
            self._notice('后台操作进行中，暂时不能应用预设。')
            return
        try:
            document, entry = self._resolve_preset(key)
            from mixdown_preset_documents import plan_application
            from preset_documents import validate_document
            document = validate_document(document, allow_builtin=True)
            include_width = 'width' in document.get('payload', {})
            with self._lock:
                before = self._state.preset_state()
                if before['tracks']:
                    plan = plan_application(
                        document, before, sample_rate=self._state.sample_rate,
                        include_width=include_width, include_tracks=False)
                    self._state.write_preset_state(plan['after'])
                else:
                    payload = document['payload']
                    current = self._state.processing_state()
                    merged = dict(current)
                    for name in ('eq_enabled', 'eq_bands', 'compression_enabled',
                                 'compression_strength', 'reverb_enabled', 'reverb_profile',
                                 'reverb_mix', 'reverb_predelay_ms'):
                        merged[name] = copy.deepcopy(payload[name])
                    if include_width:
                        merged['width'] = payload['width']
                    self._state.write_processing(merged)
                self._state.preset_name = str(entry.get('name') or key)
                self._state.message = '已应用预设“{0}”'.format(entry.get('name') or key)
            self._after_render_change(preset_name=entry.get('name') or key)
        except Exception as error:
            self._notice('预设应用失败：' + str(error))

    @Slot(str)
    def savePreset(self, name):
        if not self._ensure_entered():
            return False
        if self._active_kind:
            self._notice('后台操作进行中，暂时不能追加预设。')
            return False
        try:
            if not isinstance(name, str) or not name.strip():
                raise ValueError('预设名称不能为空。')
            from mixdown_preset_documents import make_document
            with self._lock:
                payload = self._state.processing_state()
            document = make_document(payload, name.strip(), include_width=True)
            revision = self._library.snapshot()['revision']
            result = self._library.put_document(document, expected_revision=revision)
            self._load_presets()
            self.presetsChanged.emit()
            with self._lock:
                self._state.preset_name = result['entry']['name']
                self._state.message = '已追加保存预设“{0}”。'.format(result['entry']['name'])
            self._recompute_and_emit()
            return True
        except Exception as error:
            self._notice('保存预设失败：' + str(error))
            return False

    @Slot(QUrl)
    def importPresets(self, url):
        if not self._ensure_entered():
            return False
        if self._active_kind:
            self._notice('后台操作进行中，暂时不能导入预设。')
            return False
        try:
            path = _local_url(url)
            revision = self._library.snapshot()['revision']
            result = self._library.import_file(path, expected_revision=revision)
            self._load_presets()
            self.presetsChanged.emit()
            self._notice('本机库中已可使用预设“{0}”。'.format(result['entry']['name']))
            return True
        except Exception as error:
            self._notice('导入预设失败：' + str(error))
            return False

    @Slot(QUrl, str)
    def exportPreset(self, url, key):
        if not self._ensure_entered():
            return False
        if self._active_kind:
            self._notice('后台操作进行中，暂时不能导出预设。')
            return False
        try:
            destination = _local_url(url)
            meta = self._preset_meta.get(str(key or ''))
            if meta is None:
                raise ValueError('请选择一个已保存的统一库预设。')
            result = self._library.export_file(meta['id'], meta['version'], destination)
            self._notice('预设已导出：' + str(result))
            return True
        except Exception as error:
            self._notice('导出预设失败：' + str(error))
            return False

    @Slot()
    def installMixdown(self):
        if not self._ensure_entered():
            return False
        if self._active_kind:
            self._notice('已有后台操作正在运行。')
            return False
        root, spec, installer, packages = self._refresh_component_paths()
        if root is None or spec is None or not installer or not Path(installer).is_file():
            self._apply_support_result(False, '当前版本未包含 Pedalboard 离线安装器。',
                                       packages, installed=False)
            return False
        self._support.update(visible=True, busy=True, canInstall=False, state='installing',
                             message='正在使用随包离线 wheel 安装混音组件…')
        with self._lock:
            self._state.message = self._support['message']
        self._recompute_and_emit()

        def work(stop, _send):
            report = run_component_installer(
                'install', root, installer, check=stop.is_set, force=False)
            current = optional_packages(root, spec)
            ready, detail = activate_pedalboard(current, check=stop.is_set)
            return {'ready': bool(ready), 'detail': str(detail or ''),
                    'packages': str(current or ''), 'installer': report}

        def finish(value, error):
            self._support_finish(value, error, True)

        if not self._start_owned('mixdown-install', work, finish):
            self._notice('工作通道正被其他后台操作占用，未开始安装。')
            return False
        return True

    @Slot()
    def openResult(self):
        with self._lock:
            result = self._state.result
        if not result or not Path(result).is_file():
            self._notice('当前没有可打开的导出结果。')
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(result).resolve()))):
            self._notice('系统无法打开导出文件。')

    def accept_task(self, job, artifacts):
        """Load only verified separation WAVs belonging to this exact task."""
        if not self._ensure_entered():
            return False
        try:
            with self._lock:
                if self._active_kind:
                    raise ValueError('后台操作进行中，不能切换任务。')
                job_path = Path(job).expanduser().resolve()
                if not (job_path / 'request.json').is_file():
                    raise ValueError('任务缺少 request.json。')
                rows = _artifacts_list(artifacts)
            by_relative = {}
            for row in rows:
                if row.get('state') != 'verified' or row.get('available') is False:
                    continue
                path_text = str(row.get('path') or '')
                if not path_text:
                    continue
                path = Path(path_text).expanduser().resolve()
                if not path.is_file() or not path.is_relative_to(job_path):
                    raise ValueError('任务分轨必须位于该任务目录内。')
                relative = path.relative_to(job_path).as_posix()
                if relative not in ('audio/vocals.wav', 'audio/lead.wav',
                                    'audio/backing.wav', 'audio/instrumental.wav'):
                    continue
                if row.get('role') != 'audio':
                    continue
                identity = {
                    'id': str(row.get('id') or ''), 'revision': str(row.get('revision') or ''),
                    'sha256': str(row.get('sha256') or ''), 'provenance': 'task_separation',
                    'relative': relative,
                }
                if not identity['id'] or not identity['revision'] or not identity['sha256']:
                    raise ValueError('任务分轨缺少完整产物身份。')
                by_relative[relative] = {'path': str(path), 'artifact': identity}
            backing = by_relative.get('audio/instrumental.wav')
            if backing is None:
                raise ValueError('任务缺少已核验的 audio/instrumental.wav。')
            request_config = json.loads((job_path / 'request.json').read_text(encoding='utf-8'))
            if not isinstance(request_config, dict):
                raise ValueError('任务 request.json 必须是对象。')
            voice_mode = str(request_config.get('voice_mode') or 'single')
            if voice_mode in ('dual', 'roformer'):
                if not ('audio/lead.wav' in by_relative
                        and 'audio/backing.wav' in by_relative):
                    raise ValueError('双声部任务缺少已核验的 lead/backing 分轨。')
                vocal_names = (('audio/lead.wav', '主唱分轨'),
                               ('audio/backing.wav', '和声分轨'))
            elif voice_mode == 'single':
                if 'audio/vocals.wav' not in by_relative:
                    raise ValueError('单声部任务缺少已核验的 vocals.wav。')
                vocal_names = (('audio/vocals.wav', '人声分轨'),)
            elif 'audio/lead.wav' in by_relative and 'audio/backing.wav' in by_relative:
                vocal_names = (('audio/lead.wav', '主唱分轨'),
                               ('audio/backing.wav', '和声分轨'))
            elif 'audio/vocals.wav' in by_relative:
                vocal_names = (('audio/vocals.wav', '人声分轨'),)
            else:
                raise ValueError('任务缺少已核验的真实歌声分轨。')
            if len(vocal_names) > MAX_VOCALS:
                raise ValueError('任务歌声分轨数量超过混音上限。')
            sources = []
            for index, (name, label) in enumerate(vocal_names):
                item = by_relative[name]
                sources.append({'id': f'vocal-{index + 1}', 'label': label,
                                'path': item['path'], 'artifact': item['artifact']})
            sources.append({'id': 'instrumental', 'label': '伴奏',
                            'path': backing['path'], 'artifact': backing['artifact']})
            with self._lock:
                old_has_media = self._state.has_media()
                self._state.load_task_sources(job_path, sources)
                self._state.preset_name = '自定义'
                self._result_fingerprint = ''
                self._export_plan, self._export_receipt, self._export_task_artifacts = None, None, []
                self._state.message = '已载入任务原分轨；它们不是调音后或合成歌声。'
            if old_has_media and self._preview is not None:
                self._preview.release()
            self._clear_hotspots()
            self._schedule_preview(0)
            self._recompute_and_emit()
            return True
        except Exception as error:
            self._notice('载入任务分轨失败：' + str(error))
            return False

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._preview_timer.stop()
        self._eq_timer.stop()
        self._poll_timer.stop()
        self._freshness_timer.stop()
        self._support_retry.stop()
        if self._active_kind:
            self._worker.cancel()
        self._support_lane.close()
        if self._preview is not None:
            try:
                self._preview.release()
            except Exception:
                pass
        if self._player is not None:
            try:
                self._player.close(timeout=3.0)
            except Exception:
                pass
            self._player = None
        self._discard_cache_dir()
        self._cache_dir = None


__all__ = ['QuickMixdown']
