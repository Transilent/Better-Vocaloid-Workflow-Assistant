"""Pure download-source routing tests; no GUI, model download or user state."""
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

import download_sources as sources
import updater


GITHUB_RELEASE = 'https://github.com/acme/tool/releases/download/v1.2.3/tool.zip'
GITHUB_RAW = 'https://raw.githubusercontent.com/acme/tool/main/README.md'
GITHUB_ARCHIVE = 'https://github.com/acme/tool/archive/refs/tags/v1.2.3.zip'
HF_RESOLVE = 'https://huggingface.co/acme/tool/resolve/0123/config.json'
PYPI_WHEEL = 'https://files.pythonhosted.org/packages/bc/4e/0c39ecb4c2e8b76a20364a112e332aaa198b50a11fe1b9d3a871ea836b48/pymss-2.1.8-py3-none-any.whl'
PYTORCH_CUDA = 'https://download.pytorch.org/whl/cu128/torch-2.7.1%2Bcu128-cp312-cp312-win_amd64.whl'
API_METADATA = 'https://api.github.com/repos/acme/tool/releases/latest'


class _Response(io.BytesIO):
    def __init__(self, data=b'{}', status=200, content_length=None):
        super().__init__(data)
        self.status = status
        self.headers = {}
        if content_length is not None:
            self.headers['Content-Length'] = str(content_length)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class DownloadSourceTests(unittest.TestCase):
    def test_public_options_and_legacy_modes(self):
        self.assertEqual(sources.SOURCE_OPTIONS[0][1], 'auto')
        self.assertEqual(sources.SOURCE_OPTIONS[1][1], 'direct')
        self.assertEqual(sources.SOURCE_OPTIONS[2][1], 'mirror')
        self.assertEqual(sources.SOURCE_OPTIONS[:3], (
            ("自动选择", "auto"), ("官方源直连", "direct"), ("优先加速源", "mirror")))
        self.assertEqual(sources.SOURCES, tuple(value for _, value in sources.SOURCE_OPTIONS))
        for source in sources.SOURCES:
            self.assertIn(source, sources.SOURCES)
            self.assertEqual(len(sources.source_options()), len(sources.SOURCES))
        self.assertEqual(sources.normalize_source('official'), 'direct')
        self.assertEqual(sources.normalize_source('ghproxy'), 'gh-proxy')
        self.assertEqual(sources.legacy_source('mirror'), 'mirror')
        with self.assertRaises(ValueError):
            sources.legacy_source('ghfast')
        with self.assertRaises(ValueError):
            sources.normalize_source('unknown')

    def test_old_single_route_api_is_unchanged(self):
        self.assertEqual(
            sources.accelerated_url(GITHUB_RELEASE),
            'https://api.gitproxy.dev/github.com/acme/tool/releases/download/v1.2.3/tool.zip')
        hf = HF_RESOLVE + '?download=true'
        self.assertEqual(
            sources.accelerated_url(hf),
            'https://hf-mirror.com/acme/tool/resolve/0123/config.json?download=true')
        self.assertEqual(len(sources.download_urls('https://github.com/a/b', 'auto')), 2)
        self.assertEqual(len(sources.download_urls('https://github.com/a/b', 'direct')), 1)

    def test_github_multi_route_order_and_explicit_sources(self):
        routes = sources.download_urls(GITHUB_RELEASE, 'auto')
        self.assertEqual(routes[0], GITHUB_RELEASE)
        self.assertIn('https://api.gitproxy.dev/github.com/acme/tool/releases/download/v1.2.3/tool.zip', routes)
        self.assertIn('https://gh-proxy.com/' + GITHUB_RELEASE, routes)
        self.assertIn('https://ghfast.top/' + GITHUB_RELEASE, routes)
        mirror = sources.download_urls(GITHUB_RELEASE, 'mirror')
        self.assertTrue(mirror[0].startswith('https://api.gitproxy.dev/'))
        self.assertIn(GITHUB_RELEASE, mirror)
        self.assertTrue(sources.download_urls(GITHUB_RELEASE, 'gh-proxy')[0].startswith('https://gh-proxy.com/'))
        self.assertTrue(sources.download_urls(GITHUB_RELEASE, 'ghfast')[0].startswith('https://ghfast.top/'))
        self.assertEqual(sources.download_urls(GITHUB_RELEASE, 'hf-mirror'), [GITHUB_RELEASE])

    def test_huggingface_mirror_and_whitelisted_kinds(self):
        self.assertEqual(sources.public_download_kind(GITHUB_RELEASE), 'github-release')
        self.assertEqual(sources.public_download_kind(GITHUB_RAW), 'github-raw')
        self.assertEqual(sources.public_download_kind(GITHUB_ARCHIVE), 'github-archive')
        self.assertEqual(sources.public_download_kind(HF_RESOLVE), 'hf-resolve')
        auto = sources.download_urls(HF_RESOLVE, 'auto')
        self.assertEqual(auto, [HF_RESOLVE, 'https://hf-mirror.com/acme/tool/resolve/0123/config.json'])
        self.assertEqual(
            sources.download_urls(HF_RESOLVE, 'hf-mirror')[0],
            'https://hf-mirror.com/acme/tool/resolve/0123/config.json')

    def test_public_metadata_remains_direct(self):
        for source in sources.SOURCES:
            self.assertEqual(sources.download_urls(API_METADATA, source), [API_METADATA])

    def test_secrets_and_non_download_pages_are_never_rewritten(self):
        unsafe = (
            'https://user:pass@github.com/acme/tool/releases/download/v1/file.zip',
            'http://github.com/acme/tool/releases/download/v1/file.zip',
            GITHUB_RELEASE + '?token=secret',
            HF_RESOLVE + '?signature=private',
            'https://github.com/acme/tool/tree/main',
            'https://github.com/acme/tool/archive/refs/heads/%2E%2E/secret.zip',
            'https://files.pythonhosted.org/packages/aa/bb/readme.txt',
            'https://download.pytorch.org/whl/cu128/readme.txt',
        )
        for url in unsafe:
            for source in sources.SOURCES:
                routes = sources.download_urls(url, source)
                self.assertEqual(routes, [url], (source, url))
        self.assertEqual(sources.accelerated_url(GITHUB_RELEASE + '?token=secret'), GITHUB_RELEASE + '?token=secret')
        safe = HF_RESOLVE + '?download=true'
        self.assertIn('https://hf-mirror.com/', sources.download_urls(safe, 'mirror')[0])

    def test_source_names(self):
        self.assertEqual(sources.source_name('https://api.gitproxy.dev/github.com/a/b'), 'GitProxy')
        self.assertEqual(sources.source_name('https://gh-proxy.com/https://github.com/a/b'), 'GH-Proxy')
        self.assertEqual(sources.source_name('https://ghfast.top/https://github.com/a/b'), 'ghfast.top')
        self.assertEqual(sources.source_name('https://hf-mirror.com/a/b'), 'HF-Mirror')

    def test_read_small_retries_and_preserves_bounds(self):
        calls = []

        def opener(request, timeout):
            calls.append((request.full_url, request.headers, timeout))
            if len(calls) == 1:
                raise URLError('unavailable')
            return _Response(b'{"version":"1.0.0"}')

        data = sources.read_small(GITHUB_ARCHIVE, 'mirror', limit=1024, opener=opener)
        self.assertEqual(data, b'{"version":"1.0.0"}')
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][2], 12)
        self.assertNotIn('Authorization', calls[0][1])
        self.assertNotIn('Cookie', calls[0][1])

    def test_read_small_rejects_all_oversized_responses(self):
        def opener(_request, timeout):
            return _Response(b'x' * 17, content_length=17)

        with self.assertRaises(ValueError):
            sources.read_small(HF_RESOLVE, 'direct', limit=16, opener=opener)

    def test_read_small_reports_network_failure(self):
        opener = Mock(side_effect=URLError('offline'))
        with self.assertRaises(OSError):
            sources.read_small(GITHUB_RELEASE, 'direct', opener=opener)


    def test_updater_explicit_source_prefers_published_inventory(self):
        calls = []

        def reader(url, source):
            calls.append((url, source))
            return json.dumps({'version': '9.9.9', 'release_notes': 'notes'}).encode()

        with patch.object(updater, 'is_newer', return_value=True), \
             patch.object(updater, 'compatible_update', return_value=None):
            result = updater.check_release('gh-proxy', reader)
        self.assertEqual(result['version'], '9.9.9')
        self.assertTrue(calls and calls[0][0].endswith('/release-assets.json'))
        self.assertEqual(calls[0][1], 'gh-proxy')
        self.assertTrue(all('api.github.com' not in url for url, _ in calls))

    def test_updater_auto_starts_with_direct_metadata(self):
        calls = []

        def reader(url, source):
            calls.append((url, source))
            if url == updater.API:
                return json.dumps({'tag_name': 'v9.9.9', 'draft': False,
                                   'prerelease': False, 'body': 'notes'}).encode()
            return json.dumps({'version': '9.9.9'}).encode()

        with patch.object(updater, 'is_newer', return_value=True), \
             patch.object(updater, 'compatible_update', return_value=None):
            updater.check_release('auto', reader)
        self.assertEqual(calls[0], (updater.API, 'auto'))
        self.assertTrue(all(source == 'auto' for _, source in calls))


    def test_pypi_tuna_and_aliyun_mirror_routes(self):
        self.assertEqual(sources.public_download_kind(PYPI_WHEEL), 'pypi-wheel')
        auto = sources.download_urls(PYPI_WHEEL, 'auto')
        self.assertEqual(auto[0], PYPI_WHEEL)
        self.assertIn(
            'https://mirrors.tuna.tsinghua.edu.cn/pypi/web/packages/bc/4e/0c39ecb4c2e8b76a20364a112e332aaa198b50a11fe1b9d3a871ea836b48/pymss-2.1.8-py3-none-any.whl',
            auto)
        self.assertIn(
            'https://mirrors.aliyun.com/pypi/packages/bc/4e/0c39ecb4c2e8b76a20364a112e332aaa198b50a11fe1b9d3a871ea836b48/pymss-2.1.8-py3-none-any.whl',
            auto)
        self.assertTrue(sources.download_urls(PYPI_WHEEL, 'pypi-tuna')[0].startswith(
            'https://mirrors.tuna.tsinghua.edu.cn/pypi/web/packages/'))
        self.assertTrue(sources.download_urls(PYPI_WHEEL, 'pypi-aliyun')[0].startswith(
            'https://mirrors.aliyun.com/pypi/packages/'))
        self.assertEqual(sources.download_urls(PYPI_WHEEL, 'direct'), [PYPI_WHEEL])
        self.assertEqual(sources.download_urls(PYPI_WHEEL, 'ghfast'), [PYPI_WHEEL])
        self.assertEqual(sources.download_urls(GITHUB_RELEASE, 'pypi-tuna'), [GITHUB_RELEASE])
        self.assertEqual(sources.accelerated_url(PYPI_WHEEL), PYPI_WHEEL)
        self.assertEqual(sources.source_name(
            'https://mirrors.tuna.tsinghua.edu.cn/pypi/web/packages/x'), 'TUNA PyPI')
        self.assertEqual(sources.source_name(
            'https://mirrors.aliyun.com/pypi/packages/x'), 'Aliyun PyPI')

    def test_sjtu_pytorch_channel_mirror(self):
        self.assertEqual(sources.public_download_kind(PYTORCH_CUDA), 'pytorch-wheel')
        self.assertIsNone(sources.public_download_kind('https://download.pytorch.org/whl/cu128/torch-2.7.0%2Bcu128-cp312-cp312-win_amd64.whl'))
        auto = sources.download_urls(PYTORCH_CUDA, 'auto')
        self.assertEqual(auto[0], PYTORCH_CUDA)
        self.assertEqual(auto[1],
            'https://mirror.sjtu.edu.cn/pytorch-wheels/cu128/'
            'torch-2.7.1%2Bcu128-cp312-cp312-win_amd64.whl')
        mirror = sources.download_urls(PYTORCH_CUDA, 'mirror')
        self.assertTrue(mirror[0].startswith('https://mirror.sjtu.edu.cn/pytorch-wheels/cu128/'))
        self.assertEqual(sources.download_urls(PYTORCH_CUDA, 'pytorch-sjtu')[0],
            'https://mirror.sjtu.edu.cn/pytorch-wheels/cu128/'
            'torch-2.7.1%2Bcu128-cp312-cp312-win_amd64.whl')
        self.assertEqual(sources.download_urls(GITHUB_RELEASE, 'pytorch-sjtu'), [GITHUB_RELEASE])
        self.assertEqual(sources.accelerated_url(PYTORCH_CUDA), PYTORCH_CUDA)
        self.assertEqual(
            sources.accelerated_url(PYTORCH_CUDA, 'pytorch-sjtu'),
            'https://mirror.sjtu.edu.cn/pytorch-wheels/cu128/'
            'torch-2.7.1%2Bcu128-cp312-cp312-win_amd64.whl')
        self.assertEqual(sources.source_name(
            'https://mirror.sjtu.edu.cn/pytorch-wheels/cpu/torch.whl'), 'SJTU PyTorch')


if __name__ == '__main__':
    unittest.main()
