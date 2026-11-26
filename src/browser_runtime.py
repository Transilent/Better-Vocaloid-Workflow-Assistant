"""Browser engine discovery (preferring system Edge with isolated VWA profile) and cache pruning."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import shutil

from common import ROOT, load_config, native_path

_LOG = logging.getLogger(__name__)

# Pure temporary cache directories inside a Chromium/Edge profile that can be
# pruned safely on shutdown WITHOUT touching Cookies, Local Storage, or IndexedDB.
_PRUNABLE_PROFILE_SUBDIRS = (
    'Default/Cache',
    'Default/Code Cache',
    'Default/GPUCache',
    'Default/DawnCache',
    'Default/DawnGraphiteCache',
    'Default/DawnWebGPUCache',
    'Default/GrShaderCache',
    'Default/ShaderCache',
    'Default/Service Worker/CacheStorage',
    'GrShaderCache',
    'ShaderCache',
    'GraphiteDawnCache',
)


def _windows_candidates(rel_paths: tuple[str, ...]) -> list[Path]:
    roots = []
    for env_key in ('PROGRAMFILES(X86)', 'PROGRAMFILES', 'LOCALAPPDATA'):
        raw = os.environ.get(env_key, '').strip()
        if raw:
            roots.append(Path(raw))
    found = []
    for base in roots:
        for rel in rel_paths:
            candidate = base / rel
            if candidate.is_file():
                found.append(candidate)
    return found


def find_system_edge() -> Path | None:
    """Return installed Microsoft Edge executable if available on this host."""
    if os.name == 'nt':
        hits = _windows_candidates((
            'Microsoft/Edge/Application/msedge.exe',
        ))
        if hits:
            return hits[0]
    which = shutil.which('msedge') or shutil.which('microsoft-edge')
    return Path(which) if which else None


def find_system_chrome() -> Path | None:
    """Return installed Google Chrome executable if available on this host."""
    if os.name == 'nt':
        hits = _windows_candidates((
            'Google/Chrome/Application/chrome.exe',
        ))
        if hits:
            return hits[0]
    which = shutil.which('chrome') or shutil.which('google-chrome')
    return Path(which) if which else None


def _is_default_bundled_browser_path(executable: str, root: Path) -> bool:
    try:
        target = Path(executable).resolve()
        bundled = (root / 'dependencies/browser').resolve()
        return target.is_relative_to(bundled)
    except (OSError, ValueError):
        return 'dependencies/browser' in executable.replace('\\', '/').lower()


def browser_engine_options(headless: bool = False, *, root: Path | None = None, config: dict | None = None) -> dict:
    """Resolve Playwright launch options using an isolated VWA temp/profile directory.

    Priority:
    1. Existing configured ``publish_browser_executable`` (e.g. bundled Chromium or explicit user path).
    2. Installed system Microsoft Edge (``channel="msedge"``) when bundled Chromium is absent or omitted.
    3. Installed system Google Chrome (``channel="chrome"``).
    4. Explicit configured channel fallback.
    """
    app_root = Path(root) if root is not None else ROOT
    temp = app_root / 'cache/browser-temp'
    temp.mkdir(parents=True, exist_ok=True)
    os.environ['TEMP'] = os.environ['TMP'] = str(temp)
    cfg = config if config is not None else load_config()
    executable = cfg.get('publish_browser_executable')
    if executable:
        if Path(executable).is_file():
            return {'executable_path': executable, 'headless': headless, 'args': ['--no-proxy-server']}
        # If the bundled dependencies/browser/chrome binary was intentionally omitted
        # to save ~493 MiB, transparently fall back to system Edge / Chrome while
        # keeping VWA's completely isolated login profile.
        if _is_default_bundled_browser_path(str(executable), app_root):
            if find_system_edge() is not None:
                return {'channel': 'msedge', 'headless': headless, 'args': ['--no-proxy-server']}
            if find_system_chrome() is not None:
                return {'channel': 'chrome', 'headless': headless, 'args': ['--no-proxy-server']}
        raise FileNotFoundError('未找到可用浏览器：请安装 Microsoft Edge / Chrome，或恢复 dependencies/browser/chrome 目录。')

    configured_channel = cfg.get('publish_browser')
    if configured_channel:
        return {'channel': configured_channel, 'headless': headless, 'args': ['--no-proxy-server']}
    if find_system_edge() is not None:
        return {'channel': 'msedge', 'headless': headless, 'args': ['--no-proxy-server']}
    return {'channel': 'chrome', 'headless': headless, 'args': ['--no-proxy-server']}


def browser_options(headless: bool = False, *, root: Path | None = None, config: dict | None = None) -> dict:
    return {
        **browser_engine_options(headless, root=root, config=config),
        'viewport': {'width': 1440, 'height': 960},
        'locale': 'zh-CN',
    }


def restore_migrated_session(context, profile) -> bool:
    """One-time transfer of this assistant's existing dedicated login session."""
    path = Path(profile) / 'browser-migration-session.json'
    if not path.is_file():
        return False
    state = json.loads(path.read_text(encoding='utf-8'))
    if state.get('cookies'):
        context.add_cookies(state['cookies'])
    origins = json.dumps(state.get('origins', []), ensure_ascii=True)
    context.add_init_script(
        'const vfSavedOrigins=' + origins + ';'
        'for(const o of vfSavedOrigins){if(location.origin===o.origin){'
        'for(const item of o.localStorage||[])localStorage.setItem(item.name,item.value);}}'
    )
    path.unlink()
    return True


def prune_browser_profile_cache(profile) -> int:
    """Delete disposable Chromium disk caches while strictly preserving login state."""
    base = Path(profile)
    if not base.is_dir():
        return 0
    reclaimed = 0
    for rel in _PRUNABLE_PROFILE_SUBDIRS:
        target = base / rel
        if not target.is_dir() or target.is_symlink():
            continue
        try:
            for item in target.rglob('*'):
                if item.is_file():
                    try:
                        reclaimed += item.stat().st_size
                    except OSError:
                        pass
            shutil.rmtree(native_path(target), ignore_errors=False)
        except OSError as exc:
            _LOG.debug('prune browser cache %s skipped: %s', target, exc)
    return reclaimed
