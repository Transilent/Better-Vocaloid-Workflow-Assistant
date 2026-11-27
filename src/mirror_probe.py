"""Happy Eyeballs concurrent mirror latency probe with 24h health cache.

Pure-Python stdlib module (zero Qt imports). Probes candidate download hosts
concurrently via lightweight HEAD requests (3s timeout) and persists host
latency/failure telemetry under ``cache/mirror-health.json`` so subsequent
auto-routed downloads prefer fast healthy mirrors and demote failed ones.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import time
from typing import Any, Callable, Sequence
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from vwa_net import direct_urlopen
from vwa_paths import ROOT

HEALTH_SCHEMA = 'vwa-mirror-health/1'
HEALTH_TTL_SECONDS = 86_400.0
DEFAULT_PROBE_TIMEOUT = 3.0


def health_cache_path(root: Path = ROOT) -> Path:
    return Path(root).resolve() / 'cache' / 'mirror-health.json'


def _host_key(url: str) -> str:
    try:
        return (urlsplit(str(url or '')).hostname or '').casefold()
    except ValueError:
        return ''


def load_health_cache(*, root: Path = ROOT, now: float | None = None) -> dict[str, dict[str, Any]]:
    path = health_cache_path(root)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(data, dict) or data.get('schema') != HEALTH_SCHEMA:
        return {}
    hosts = data.get('hosts')
    if not isinstance(hosts, dict):
        return {}
    current = time.time() if now is None else float(now)
    fresh: dict[str, dict[str, Any]] = {}
    for host, row in hosts.items():
        if not isinstance(row, dict):
            continue
        ts = float(row.get('checked_at', 0.0) or 0.0)
        if 0.0 <= current - ts <= HEALTH_TTL_SECONDS:
            fresh[str(host).casefold()] = {
                'ok': bool(row.get('ok', False)),
                'latency_ms': float(row.get('latency_ms', 9999.0) or 9999.0),
                'checked_at': ts,
            }
    return fresh


def save_health_cache(hosts: dict[str, dict[str, Any]], *, root: Path = ROOT) -> None:
    path = health_cache_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp: Path | None = None
    try:
        with NamedTemporaryFile(
            mode='w', encoding='utf-8', dir=path.parent,
            prefix='.mirror-health-', suffix='.tmp', delete=False,
        ) as stream:
            tmp = Path(stream.name)
            json.dump({'schema': HEALTH_SCHEMA, 'hosts': hosts}, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def _default_head_probe(url: str, timeout: float = DEFAULT_PROBE_TIMEOUT) -> tuple[bool, float]:
    started = time.perf_counter()
    req = urllib.request.Request(url, method='HEAD', headers={'User-Agent': 'VWA-MirrorProbe/1'})
    try:
        with direct_urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, 'status', 200) or 200
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            return (200 <= int(code) < 400), elapsed_ms
    except urllib.error.HTTPError as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        # 405 Method Not Allowed still proves the host responded quickly over TLS
        return (exc.code in (405, 403)), elapsed_ms
    except (OSError, ValueError):
        return False, 9999.0


def probe_urls(
    urls: Sequence[str],
    *,
    timeout: float = DEFAULT_PROBE_TIMEOUT,
    probe_fn: Callable[[str, float], tuple[bool, float]] | None = None,
    root: Path = ROOT,
    now: float | None = None,
) -> dict[str, dict[str, Any]]:
    """Probe each candidate URL's host concurrently and update the 24h health cache."""
    probe = probe_fn or _default_head_probe
    unique_by_host: dict[str, str] = {}
    for url in urls:
        host = _host_key(url)
        if host and host not in unique_by_host:
            unique_by_host[host] = str(url)
    if not unique_by_host:
        return {}
    checked_at = time.time() if now is None else float(now)
    cache = load_health_cache(root=root, now=checked_at)
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(4, len(unique_by_host))) as pool:
        futures = {
            pool.submit(probe, target_url, timeout): host
            for host, target_url in unique_by_host.items()
        }
        for future in as_completed(futures):
            host = futures[future]
            try:
                ok, latency_ms = future.result()
            except Exception:
                ok, latency_ms = False, 9999.0
            entry = {
                'ok': bool(ok),
                'latency_ms': round(float(latency_ms), 2),
                'checked_at': checked_at,
            }
            results[host] = entry
            cache[host] = entry
    try:
        save_health_cache(cache, root=root)
    except OSError:
        pass
    return results


def rank_candidates(
    urls: Sequence[str],
    *,
    root: Path = ROOT,
    allow_probe: bool = False,
    probe_fn: Callable[[str, float], tuple[bool, float]] | None = None,
    timeout: float = DEFAULT_PROBE_TIMEOUT,
    now: float | None = None,
) -> list[str]:
    """Order candidate URLs: healthy fastest first, unchecked next, failed last."""
    items = [str(u) for u in urls if u]
    if len(items) <= 1:
        return items
    current = time.time() if now is None else float(now)
    cache = load_health_cache(root=root, now=current)
    missing = [u for u in items if _host_key(u) and _host_key(u) not in cache]
    if allow_probe and missing and (probe_fn is not None or os.environ.get('VWA_TESTING') != '1'):
        probed = probe_urls(missing, timeout=timeout, probe_fn=probe_fn, root=root, now=current)
        cache.update(probed)
    if not cache:
        return items

    def sort_key(pair: tuple[int, str]) -> tuple[int, float, int]:
        idx, url = pair
        row = cache.get(_host_key(url))
        if row is None:
            return (1, 0.0, idx)
        if row['ok']:
            return (0, float(row['latency_ms']), idx)
        return (2, float(row['latency_ms']), idx)

    return [url for _, url in sorted(enumerate(items), key=sort_key)]
