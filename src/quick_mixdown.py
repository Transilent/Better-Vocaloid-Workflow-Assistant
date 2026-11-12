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
    GR_HISTORY_BUCKET_MS,
    MixdownWorkspaceState,
    empty_gr_history,
    eq_curve,
    meters_from_telemetry,
    reverb_curve_from_telemetry,
    spectrum_from_telemetry,
    telemetry_identity,
    update_gr_history,
)
from preset_library import PresetLibrary
from common import ROOT
from quick_work import WorkerLane


_METER_INTERVAL_SECONDS = .05
_LIVE_READING_TIMEOUT_SECONDS = .30


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
    grHistoryChanged = Signal()
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
        self._preset_plan = None
        self._preset_undo = None
        self._view = {}
        self._tracks = self._state.tracks
        self._bands = self._state.bands
        self._eq_basis = (self._state.eq_enabled, self._state.sample_rate)
        self._eq_curve = []
        self._meters = {
            'lanes': [{'id': row['id'], 'rms': None, 'peak': None} for row in self._tracks],
            'gr': None, 'gr_peak': None,
            'compressor_available': False, 'compressor_enabled': None,
            'limiter': None, 'limiter_peak': None,
            'limiter_available': False, 'limiter_enabled': None,
            'reverb': {
                'available': False, 'enabled': None,
                'send_rms': None, 'send_peak': None,
                'return_rms': None, 'return_peak': None, 'dry_rms': None,
                'wet_fraction': None, 'wet_to_dry': None, 'method': '', 'source': '',
                'edc_available': False, 'edc_measured': False, 'edc_reason': '',
            },
            'tail': None, 'source': '',
        }
        self._meters['lanes'].append({'id': 'master', 'rms': None, 'peak': None})
        self._spectrum = {'before': [], 'after': [], 'freqs': [], 'source': ''}
        self._reverb_curve = []
        self._gr_history = empty_gr_history()
        self._support_ready = False
        self._support = {
            'visible': False, 'ready': False, 'busy': False, 'canInstall': False,
            'state': 'idle', 'message': '进入混音页后将在本地检查混音组件。',
            'detail': '', 'installerAvailable': False, 'playerAvailable': False,
            'installLabel': '安装混音组件',
        }
        self._export_plan = None
        self._export_receipt = None
        self._registered_mix_id = ''
        self._export_task_artifacts = []
        self._result_fingerprint = ''
        self._last_telemetry_identity = None
        self._source_edit_pending = False
        self._last_telemetry_at = 0.0
        self._last_meter_identity = None
        self._last_spectrum_window = None
        self._last_spectrum_at = 0.0
        self._last_meter_at = 0.0
        self._last_reverb_key = None
        self._last_reverb_at = 0.0
        self._resume_position_ms = None
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
        self._eq_timer.setInterval(16)
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

    @Property('QVariantMap', notify=grHistoryChanged)
    def grHistory(self):
        return copy.deepcopy(self._gr_history)

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
            for name, slot in (('errorOccurred', self._player_error), ('prepared', self._player_prepared),
                               ('seekConfirmed', self._clear_hotspots), ('telemetryChanged', self._telemetry)):
                signal = getattr(self._player, name, None)
                if signal is not None:
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
            previous = self._meters, self._spectrum, self._reverb_curve, self._gr_history
            counters = []
            for row in self._tracks:
                counters.append({'id': row['id'], 'rms': None, 'peak': None})
            counters.append({'id': 'master', 'rms': None, 'peak': None})
            self._meters = {
                'lanes': counters, 'gr': None, 'gr_peak': None,
                'compressor_available': False, 'compressor_enabled': None,
                'limiter': None, 'limiter_peak': None,
                'limiter_available': False, 'limiter_enabled': None,
                'reverb': {
                    'available': False, 'enabled': None,
                    'send_rms': None, 'send_peak': None,
                    'return_rms': None, 'return_peak': None, 'dry_rms': None,
                    'wet_fraction': None, 'wet_to_dry': None, 'method': '', 'source': '',
                    'edc_available': False, 'edc_measured': False, 'edc_reason': '',
                },
                'tail': None, 'source': '',
            }
            self._spectrum = {'before': [], 'after': [], 'freqs': [], 'source': ''}
            self._reverb_curve = []
            self._gr_history = empty_gr_history()
            self._last_telemetry_identity = None
            self._last_telemetry_at = 0.0
            self._last_meter_identity = None
            self._last_spectrum_window = None
            self._last_spectrum_at = self._last_meter_at = 0.0
            self._last_reverb_key = None
        for old, new, signal in zip(previous, (self._meters, self._spectrum, self._reverb_curve, self._gr_history),
                                    (self.metersChanged, self.spectrumChanged, self.reverbCurveChanged, self.grHistoryChanged)):
            if old != new:
                signal.emit()

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
                            and not support_probe_busy and not self._registered_mix_id)
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
        receipt_output = ((self._export_receipt or {}).get('output')
                          if isinstance(self._export_receipt, dict) else {})
        export_sha = str((receipt_output or {}).get('sha256') or '')
        export_fresh = bool(self._state.result and export_sha
                            and not self._state.result_stale and not self._active_kind)
        registered_video = bool(export_fresh and self._state.job and self._registered_mix_id)
        independent_video = bool(export_fresh and not self._state.job)
        view['canComposeVideo'] = registered_video or independent_video
        view['videoHandoffMode'] = ('registered' if registered_video else
                                    'independent' if independent_video else '')
        view['support'] = copy.deepcopy(self._support)
        view['supportVisible'] = bool(self._support['visible'])
        view['componentMissing'] = bool(self._initialized and not self._support_ready)
        view['supportMessage'] = str(self._support.get('message') or '')
        view['nativeOutputAvailable'] = bool(player is not None and player.available)
        view['presetPreview'] = copy.deepcopy(self._preset_plan['view']) if self._preset_plan else {}
        view['canUndoPreset'] = self._preset_undo is not None
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
        if eq_changed and not self._eq_timer.isActive():
            self._eq_timer.start()
        if changed:
            self.viewChanged.emit()

    def _preview_loop_range_ms(self, duration=None):
        total = (self._player.duration() if duration is None and self._player is not None
                 else self._state.duration if duration is None else duration)
        return self._state.preview_range_ms(total)

    def _invalidate_edited_source(self):
        if (self._preview is not None and self._preview.source_key is not None
                and self._preview.source_key != self._state.source_key()):
            self._source_edit_pending = True
            self._clear_hotspots()

    def _sync_position(self):
        position = self._preview.position_ms if self._preview is not None else 0
        if position != self._state.position:
            self._state.set_position(position)
            self.positionChanged.emit()

    def _after_render_change(self, *, preset_name=None, keep_preset_undo=False, during_drag=False):
        if not keep_preset_undo:
            self._preset_undo = None
        if self._preview is not None:
            self._preview.dirty = True
        with self._lock:
            self._state.preset_name = '自定义' if preset_name is None else str(preset_name)
            if self._state.result:
                self._state.result_stale = self._state.render_fingerprint() != self._result_fingerprint
        if not during_drag or not self._preview_timer.isActive():
            self._schedule_preview(delay=65 if during_drag else 90)
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
            loop_range = self._preview_loop_range_ms(duration) if self._state.loop else (0, 0)
            opening = (self._preview.source_key != source_key
                       or (not self._player.ready and not self._player.loading))
            start_ms = (self._resume_position_ms if self._resume_position_ms is not None
                        else self._preview.position_ms)
            if opening and self._state.loop:
                start_ms = loop_range[0]
            accepted = self._preview.refresh(
                request, source_key, start_ms=start_ms,
                loop_range=loop_range)
            if not accepted:
                return False
            self._source_edit_pending = False
            self._resume_position_ms = None
            if opening:
                self._clear_hotspots()
            if not self._player.ready:
                with self._lock:
                    if not self._active_kind:
                        self._state.message = '正在从常驻进程准备连续试听…'
                self._recompute_and_emit()
            return True
        except (OSError, ValueError, TypeError) as error:
            if not self._state.has_media():
                self._clear_hotspots()
            with self._lock:
                self._state.message = str(error)
            self._recompute_and_emit()
            return False

    def _player_prepared(self):
        with self._lock:
            if self._state.message == '正在从常驻进程准备连续试听…':
                self._state.message = '连续试听已就绪；可切换 A/B 并试听整曲。'
            if self._player is not None:
                self._state.set_duration(self._player.duration())
        self._recompute_and_emit()

    def _player_error(self, message):
        if self._preview is not None:
            self._preview.failed()
        self._clear_hotspots()
        with self._lock:
            self._state.message = str(message)[:1600]
        self._recompute_and_emit()

    def _apply_reverb_measurement(self, data):
        reverb = data.get('reverb') if isinstance(data.get('reverb'), dict) else {}
        kernel = reverb.get('kernel_edc') if isinstance(reverb.get('kernel_edc'), dict) else {}
        enabled = reverb.get('enabled') is True
        ready = enabled and kernel.get('available') is True and kernel.get('measured') is True
        if not ready:
            if self._reverb_curve or self._last_reverb_key is not None:
                self._reverb_curve = []
                self._last_reverb_key = None
                self.reverbCurveChanged.emit()
            return
        key = (kernel.get('key'), kernel.get('signature'),
               self._player.generation if self._player else None)
        if key == self._last_reverb_key and self._reverb_curve:
            return
        curve = reverb_curve_from_telemetry(data)
        self._last_reverb_key = key
        if curve != self._reverb_curve:
            self._reverb_curve = curve
            self.reverbCurveChanged.emit()

    def _telemetry(self, snapshot):
        if not isinstance(snapshot, dict) or self._closed or self._source_edit_pending:
            return
        data = snapshot.get('telemetry')
        if not isinstance(data, dict):
            return
        # The renderer follows the accompaniment. Updates to the displayed
        # Nyquist limit never become edits or retire a transport generation.
        rate = data.get('sample_rate')
        if isinstance(rate, (int, float)) and 8000 <= rate <= 384000:
            with self._lock:
                rate_changed = self._state.set_sample_rate(rate)
            if rate_changed:
                self._recompute_and_emit()
        now = time.monotonic()
        try:
            identity = telemetry_identity(data, snapshot)
        except (TypeError, ValueError):
            # PCM already queued at the device can keep playing while another
            # edit is pending. Do not blank a real reading for one missing tick.
            if (self._last_telemetry_identity and snapshot.get('state') == 'playing'
                    and now - self._last_telemetry_at >= _LIVE_READING_TIMEOUT_SECONDS):
                self._clear_hotspots()
            return
        previous = self._last_telemetry_identity
        frame, revision, epoch, _ = identity
        if previous and epoch != previous[2]:
            self._clear_hotspots()
        elif previous and frame < previous[0]:
            return  # Out-of-order device snapshots cannot move the meters backwards.
        self._last_telemetry_identity = identity
        self._last_telemetry_at = now
        if self._last_meter_identity != identity and (
                not self._meters.get('source') or now - self._last_meter_at >= _METER_INTERVAL_SECONDS):
            with self._lock:
                vocal_ids = [row['id'] for row in self._state.active_vocals()]
            self._last_meter_at = now
            self._last_meter_identity = identity
            self._meters = meters_from_telemetry(data, vocal_ids)
            self.metersChanged.emit()
        if previous != identity:
            history = update_gr_history(self._gr_history, data)
            if history != self._gr_history:
                old = self._gr_history
                self._gr_history = history
                if (old['epoch'], old['revision'], int(old['positionMs'] // GR_HISTORY_BUCKET_MS), old['source']) != (
                        history['epoch'], history['revision'], int(history['positionMs'] // GR_HISTORY_BUCKET_MS), history['source']):
                    self.grHistoryChanged.emit()
        source = data.get('spectrum') or {}
        window_end = source.get('window_end_frame') if isinstance(source, dict) else None
        window = (epoch, revision, window_end if window_end is not None else frame)
        if window != self._last_spectrum_window:
            spectrum = spectrum_from_telemetry(data)
            if spectrum['freqs']:
                self._last_spectrum_window = window
                self._last_spectrum_at = now
                if spectrum != self._spectrum:
                    self._spectrum = spectrum
                    self.spectrumChanged.emit()
        if (self._spectrum['freqs'] and snapshot.get('state') == 'playing'
                and now - self._last_spectrum_at >= _LIVE_READING_TIMEOUT_SECONDS):
            self._spectrum = {'before': [], 'after': [], 'freqs': [], 'source': ''}
            self.spectrumChanged.emit()
        self._apply_reverb_measurement(data)
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
            self._invalidate_edited_source()
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
            else:
                self._invalidate_edited_source()
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
            self._after_render_change(during_drag=True)
        except Exception as error:
            self._notice(error)

    @Slot(int)
    def resetBand(self, index):
        if not self._ensure_entered():
            return
        try:
            with self._lock:
                self._state.reset_band(index)
                self._state.message = '此 EQ 频段已恢复默认。'
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
                can_audition = bool(self._support_ready and self._player is not None
                                    and self._player.available and self._state.has_media())
            if self._preview is not None:
                self._preview.select('before' if selection == 'A' else 'after')
                if can_audition:
                    self._preview.set_playing(True)
            if can_audition and (self._preview.dirty or not self._player.ready):
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
            self._notice(error)

    @Slot()
    def cancelSeek(self):
        """Drop a local drag gesture without pausing or releasing the session."""
        if self._preview is None:
            return
        self._preview.dragging = False
        self._recompute_and_emit()

    @Slot(float, float)
    def setPreviewRange(self, start, duration):
        if not self._ensure_entered():
            return False
        with self._lock:
            changed = self._state.set_preview_range(start, duration)
            loop_range = self._preview_loop_range_ms()
        if self._player is not None:
            self._player.set_loop_range(*loop_range)
        if changed and self._preview is not None and self._player is not None and self._player.ready:
            self._preview.commit_seek(loop_range[0])
        self._recompute_and_emit()
        return changed

    @Slot(bool)
    def setLoop(self, enabled):
        if not self._ensure_entered():
            return
        with self._lock:
            self._state.set_loop(enabled)
            loop_range = self._preview_loop_range_ms()
        if self._player is not None:
            self._player.set_loop(bool(enabled))
            if enabled:
                self._player.set_loop_range(*loop_range)
                position = self._player.position()
                if self._preview is not None and self._player.ready and not (
                        loop_range[0] <= position < loop_range[1]):
                    self._preview.commit_seek(loop_range[0])
            else:
                self._player.set_loop_range(0, 0)
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
            self._state.set_export_progress(
                {'fraction': 0.0, 'phase': 'prepare', 'message': '准备导出'})

        def progress(event):
            with self._lock:
                self._state.set_export_progress(event)
            self._recompute_and_emit()

        def work(stop, send):
            report = mixdown_export.execute(plan, stop.is_set, progress=send)
            if not report.get('output_sha256'):
                report['output_sha256'] = file_sha256(plan.destination, stop.is_set)
            return report

        def finish(report, error):
            if isinstance(error, (InterruptedError, MixdownCancelled)):
                with self._lock:
                    self._state.set_export_progress(
                        {'fraction': 0.0, 'phase': 'cancelled', 'message': '导出已取消'})
                self._notice('导出已取消；临时 WAV 已清理，目标文件未被写入。')
                return
            if error is not None:
                with self._lock:
                    self._state.set_export_progress(
                        {'fraction': 0.0, 'phase': 'failed', 'message': str(error)})
                self._notice('导出失败：' + str(error))
                return
            try:
                receipt = self._build_export_receipt(report, context, source_rows, task_artifacts)
                with self._lock:
                    fresh = self._state.render_fingerprint() == fingerprint
                    self._export_plan, self._export_receipt, self._export_task_artifacts = plan, receipt, task_artifacts
                    self._registered_mix_id = ''
                    self._result_fingerprint = fingerprint
                    self._state.result = str(Path(report['output_path']).resolve())
                    self._state.result_stale = not fresh or not plan.signatures_unchanged()
                    self._state.set_export_progress(
                        {'fraction': 1.0, 'phase': 'done', 'message': '导出完成'})
                    if self._state.result_stale:
                        self._state.message = '已保留导出 WAV；当前输入或参数已变化，请重新导出后登记。'
                    else:
                        self._state.message = ('WAV 已导出；检查后可人工登记为任务 Mixdown。'
                                               if source_job else
                                               'WAV 已导出；独立文件无需任务登记。')
                self._recompute_and_emit()
            except Exception as exc:
                self._notice('导出完成但结果回执无效：' + str(exc))
        if not self._start_owned('mixdown-export', work, finish, progress=progress):
            with self._lock:
                self._state.clear_export_progress()
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
        if self._registered_mix_id:
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
                rows = inspect(job, check=stop.is_set,
                               artifact_ids=[identifier for identifier, _, _ in expected_artifacts])
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
            return artifact_id, str(destination)

        def finish(value, error):
            if isinstance(error, (InterruptedError, MixdownCancelled)):
                self._notice('登记已取消；没有写入任务。')
                return
            if error is not None:
                self._notice('登记失败：' + str(error))
                return
            artifact_id, destination = value
            with self._lock:
                self._registered_mix_id = artifact_id
                self._state.message = '已登记混音，可以继续合成视频。'
                self._state.result_stale = False
            self._recompute_and_emit()

        if not self._start_owned('mixdown-register', work, finish):
            self._notice('工作通道正被其他后台操作占用，未开始登记。')

    @Slot()
    def composeVideo(self):
        if not self._view.get('canComposeVideo'):
            return
        output = (self._export_receipt or {}).get('output')
        sha256 = str((output or {}).get('sha256') or '')
        if not sha256:
            return
        if self._state.job:
            self.parent().open_registered_mix(
                self._state.job, self._registered_mix_id, self._state.result, sha256)
        else:
            self.parent().open_independent_mix(self._state.result, sha256)

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

    def _preset_stamp(self):
        return (self._state.job, self._state.output, self._state.sample_rate,
                self._state.tracks, self._state.preset_state(), self._state.source_key())

    @Slot(str, bool, bool, result=bool)
    def previewPreset(self, key, include_width=False, include_tracks=False):
        if not self._ensure_entered():
            return False
        if self._active_kind:
            self._notice('后台操作进行中，暂时不能预览预设。')
            return False
        try:
            from mixdown_preset_documents import plan_application
            document, entry = self._resolve_preset(key)
            label = str(entry.get('name') or key)
            with self._lock:
                stamp = self._preset_stamp()
                plan = plan_application(document, stamp[4], sample_rate=stamp[2],
                                        include_width=include_width, include_tracks=include_tracks)
                self._preset_plan = {'stamp': stamp, 'plan': plan, 'view': {
                    'key': key, 'name': label, 'changes': plan['changes'], 'adjusted': plan['adjusted'],
                    'hasWidth': 'width' in document['payload'], 'hasTracks': 'track_template' in document['payload'],
                    'includeWidth': include_width, 'includeTracks': include_tracks,
                }}
            self._recompute_and_emit()
            return True
        except Exception as error:
            self._preset_plan = None
            self._notice('预设预览失败：' + str(error))
            return False

    @Slot()
    def discardPresetPreview(self):
        if self._preset_plan is not None:
            self._preset_plan = None
            self._recompute_and_emit()

    @Slot(result=bool)
    def confirmPreset(self):
        if self._active_kind or not self._preset_plan:
            return False
        try:
            with self._lock:
                pending = self._preset_plan
                current, _entry = self._resolve_preset(pending['view']['key'])
                if self._preset_stamp() != pending['stamp'] or current != pending['plan']['document']:
                    raise ValueError('混音或预设文件已变更，请重新预览。')
                previous, old_name = self._state.preset_state(), self._state.preset_name
                candidate = self._state.copy()
                candidate.write_preset_state(pending['plan']['after'])
                label = pending['view']['name']
                candidate.message = '已应用预设“{0}”。'.format(label)
                self._state = candidate
                self._preset_undo = (self._preset_stamp(), previous, old_name)
                self._preset_plan = None
            self._after_render_change(preset_name=label, keep_preset_undo=True)
            return True
        except Exception as error:
            self._preset_plan = None
            self._notice('预设应用失败：' + str(error))
            return False

    @Slot(result=bool)
    def undoPreset(self):
        if self._active_kind or self._preset_undo is None:
            return False
        try:
            with self._lock:
                stamp, previous, old_name = self._preset_undo
                if self._preset_stamp() != stamp:
                    raise ValueError('混音设置或源文件已变化，不能覆盖后续编辑。')
                candidate = self._state.copy()
                candidate.write_preset_state(previous)
                candidate.message = '已撤销最近一次预设应用。'
                self._state = candidate
                self._preset_undo = None
            self._after_render_change(preset_name=old_name)
            return True
        except Exception as error:
            self._preset_undo = None
            self._notice('不能撤销预设：' + str(error))
            return False

    @Slot(str, bool, result=bool)
    def savePreset(self, name, include_width=False):
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
            document = make_document(payload, name.strip(), include_width=include_width)
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
                self._preset_plan = None
                self._preset_undo = None
                self._result_fingerprint = ''
                self._export_plan, self._export_receipt, self._export_task_artifacts = None, None, []
                self._registered_mix_id = ''
                self._state.clear_export_progress()
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
