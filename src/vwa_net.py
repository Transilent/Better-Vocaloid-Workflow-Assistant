"""Zero-cycle outbound HTTP/proxy helpers shared across updater, downloads, and diagnostics."""
from __future__ import annotations

import json
import urllib.request

from vwa_paths import ROOT


def network_proxy_mode(root=None) -> str:
    """Return 'system' only when explicitly enabled in config.json; default stays 'direct'."""
    cfg_path = (ROOT if root is None else root) / "config.json"
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
        mode = data.get("network_proxy_mode", "direct") if isinstance(data, dict) else "direct"
        return "system" if mode == "system" else "direct"
    except (OSError, ValueError, TypeError):
        return "direct"


def direct_urlopen(request, timeout=30, *, allow_system_proxy=False):
    """Open public metadata/downloads; honor system proxy only when explicitly opted in."""
    if allow_system_proxy and network_proxy_mode() == "system":
        return urllib.request.urlopen(request, timeout=timeout)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(request, timeout=timeout)
