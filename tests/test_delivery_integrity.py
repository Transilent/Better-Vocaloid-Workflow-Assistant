"""Release metadata, launcher metadata and split-part boundary tests."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

APP = Path(__file__).resolve().parents[1]
POWERSHELL = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
sys.path.insert(0, str(APP))
from tools import build_update_catalog
from tools import build_windows_launcher as launcher
from tools.build_update_catalog import build as build_catalog

VERSION = '9.9.9'
RUNTIME = 'dependencies/runtime/engine.txt'


def record(name, data):
    return {'file': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def make_bootstrap(folder, item, name):
    members = {
        'app.py': b'# first hop',
        'version.json': json.dumps({
            'version': VERSION, 'channel': 'stable', 'upgrade_pending': True,
            'runtime_upgrade': item,
        }).encode(),
        'tools/update_apply.py': (APP / 'tools/update_apply.py').read_bytes(),
    }
    inventory = {
        'version': VERSION,
        'runtime_sha256': item['source_runtime_sha256'],
        'entries': [record(path, data) for path, data in members.items()],
    }
    target = folder / name
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED) as zipped:
        for path, data in members.items():
            zipped.writestr(path, data)
        zipped.writestr('update-inventory.json', json.dumps(inventory).encode())
    return {
        'name': target.name, 'bytes': target.stat().st_size,
        'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
        'runtime_sha256': item['source_runtime_sha256'],
    }


def make_migrations(scratch, label, target_data, case=None):
    base = scratch / label
    assets = base / 'assets'
    assets.mkdir(parents=True)
    target_path = base / 'target-manifest.json'
    target_path.write_bytes(target_data)
    target_sha = hashlib.sha256(target_data).hexdigest()
    payload = {
        'app.py': b'# app',
        'version.json': json.dumps({'version': VERSION, 'channel': 'stable'}).encode(),
        'tools/update_apply.py': (APP / 'tools/update_apply.py').read_bytes(),
        'pitch_accuracy.py': b'# pitch',
        'dependencies/manifest.json': target_data,
        RUNTIME: b'new engine',
    }
    migrations = []
    for source, name in (('0.4.0', 'BVWA-Program-Update.zip'),
                         ('0.4.1', 'BVWA-App-Update.zip')):
        source_sha = hashlib.sha256(source.encode()).hexdigest()
        plan = {
            'format': 1, 'version': VERSION,
            'source_runtime_sha256': source_sha,
            'target_runtime_sha256': target_sha,
            'entries': [record(path, data) for path, data in payload.items()],
            'removed': [],
        }
        archive_entries = dict(payload)
        extra_members = []
        plan_target = target_sha
        if case == 'missing-member':
            archive_entries.pop(RUNTIME)
        elif case == 'extra-member':
            extra_members.append(('dependencies/runtime/evil.txt', b'evil'))
        elif case == 'tampered-bytes':
            archive_entries[RUNTIME] = b'other engine'
        elif case == 'removed-target':
            plan['removed'] = [record('models/keep.bin', b'kept')]
        elif case == 'removed-traversal':
            plan['removed'] = [record('../outside.bin', b'outside')]
        elif case == 'removed-bad-hash':
            plan['removed'] = [dict(record('models/removed.bin', b'old'), sha256='x' * 64)]
        elif case == 'target-not-bound':
            plan_target = 'c' * 64
            plan['target_runtime_sha256'] = plan_target
        runtime = assets / ('BVWA-Runtime-Upgrade-v' + source + '.zip')
        with zipfile.ZipFile(runtime, 'x', compression=zipfile.ZIP_DEFLATED) as zipped:
            for path, data in archive_entries.items():
                zipped.writestr(path, data)
            if case == 'duplicate-member':
                zipped.writestr('app.py', payload['app.py'])
            for path, data in extra_members:
                zipped.writestr(path, data)
            zipped.writestr('runtime-upgrade.json', json.dumps(plan).encode())
        item = {
            'name': runtime.name, 'version': VERSION,
            'bytes': runtime.stat().st_size,
            'sha256': hashlib.sha256(runtime.read_bytes()).hexdigest(),
            'source_runtime_sha256': source_sha,
            'target_runtime_sha256': plan_target,
        }
        migrations.append({
            'source_version': source,
            'update': make_bootstrap(assets, item, name),
            'runtime_upgrade': item,
        })
    return assets, migrations, target_path


def test_catalog_binding_and_archive_fail_closed():
    with tempfile.TemporaryDirectory(dir=APP / 'work', prefix='delivery-catalog-') as directory:
        scratch = Path(directory)
        probe_root = scratch / 'probe-root'
        target_data = json.dumps({
            'format': 1,
            'entries': [record(RUNTIME, b'new engine'), record('models/keep.bin', b'kept')],
        }).encode()
        write(probe_root / 'dependencies/manifest.json', target_data)
        original_root = build_update_catalog.ROOT
        build_update_catalog.ROOT = probe_root
        try:
            assets, migrations, _ = make_migrations(scratch, 'default', target_data)
            catalog = build_catalog(
                assets, VERSION, migrations, legacy_update='0.4.1', legacy_program='0.4.0')
            assert catalog['target_runtime_sha256'] == hashlib.sha256(target_data).hexdigest()
            assert 'target_runtime_manifest' not in catalog

            write(probe_root / 'dependencies/manifest.json', json.dumps({
                'entries': [record('models/old.bin', b'old')],
            }).encode())
            assets, migrations, target_path = make_migrations(scratch, 'external', target_data)
            catalog = build_catalog(
                assets, VERSION, migrations, legacy_update='0.4.1', legacy_program='0.4.0',
                target_manifest=target_path, external_reason='fixture external checkout')
            assert catalog['target_runtime_manifest']['external_build_reason'] == 'fixture external checkout'

            for case in (
                'missing-member', 'extra-member', 'duplicate-member', 'tampered-bytes',
                'removed-target', 'removed-traversal', 'removed-bad-hash', 'target-not-bound',
            ):
                assets, migrations, target_path = make_migrations(scratch, 'bad-' + case, target_data, case)
                try:
                    build_catalog(
                        assets, VERSION, migrations, legacy_update='0.4.1', legacy_program='0.4.0',
                        target_manifest=target_path, external_reason='fixture negative')
                except Exception:
                    pass
                else:
                    raise AssertionError('Catalog accepted malformed case: ' + case)
                assert not (assets / 'release-assets.json').exists()
        finally:
            build_update_catalog.ROOT = original_root


def test_launcher_metadata_is_rendered_from_version_json():
    version = json.loads((APP / 'packaging/version.json').read_text(encoding='utf-8'))['version']
    expected = launcher.assembly_version(version)
    template = (APP / 'tools/windows/Launcher.cs').read_text(encoding='utf-8')
    rendered = launcher.render_launcher_source(template, version)
    assert launcher.ASSEMBLY_VERSION_TOKEN not in rendered
    assert launcher.INFORMATIONAL_VERSION_TOKEN not in rendered
    assert 'AssemblyVersion("%s")' % expected in rendered
    assert 'AssemblyFileVersion("%s")' % expected in rendered
    assert launcher.assembly_version('1.2.3-rc.1') == '1.2.3.0'
    try:
        launcher.render_launcher_source('[assembly: AssemblyVersion("0.4.0.0")]', version)
    except ValueError:
        pass
    else:
        raise AssertionError('Hardcoded launcher source was accepted as a template')

    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    if not compiler.is_file():
        return
    with tempfile.TemporaryDirectory(dir=APP / 'work', prefix='delivery-launcher-') as directory:
        scratch = Path(directory)
        source = scratch / 'Launcher.generated.cs'
        executable = scratch / 'Launcher.version-test.exe'
        source.write_text(rendered, encoding='utf-8')
        subprocess.run([
            str(compiler), '/nologo', '/target:winexe', '/platform:x64', '/optimize+',
            '/reference:System.Windows.Forms.dll', '/utf8output', '/codepage:65001',
            '/out:' + str(executable), str(source),
        ], check=True, capture_output=True, timeout=60)
        escaped = str(executable).replace("'", "''")
        query = ("$v=[Diagnostics.FileVersionInfo]::GetVersionInfo('%s');"
                 "Write-Output ($v.FileVersion + '|' + $v.ProductVersion)" % escaped)
        result = subprocess.run(
            [str(POWERSHELL), '-NoProfile', '-Command', query],
            capture_output=True, text=True, timeout=30, check=True)
        file_version, product_version = result.stdout.strip().split('|', 1)
        assert file_version == expected
        assert product_version == version


def test_portable_parts_stay_in_release_folder():
    if os.name != 'nt':
        return
    script = APP / 'tools/Merge-PortableParts.ps1'
    with tempfile.TemporaryDirectory(dir=APP / 'work', prefix='delivery-parts-') as directory:
        release = Path(directory)
        archive_name = 'BVWA-Windows-x64.zip'
        archive_data = b'portable archive bytes for merge test'
        chunks = (archive_data[:13], archive_data[13:])
        parts = []
        for number, data in enumerate(chunks, 1):
            path = release / ('%s.%03d' % (archive_name, number))
            path.write_bytes(data)
            parts.append({'name': path.name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        metadata = {
            'archive': {'name': archive_name, 'bytes': len(archive_data),
                        'sha256': hashlib.sha256(archive_data).hexdigest()},
            'parts': parts,
        }
        inventory = release / 'release-assets.json'
        inventory.write_text(json.dumps(metadata), encoding='utf-8')

        def run_merge(output, merge_script=script):
            return subprocess.run([
                str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass',
                '-File', str(merge_script), '-FirstPart', str(release / (archive_name + '.001')),
                '-Output', str(output),
            ], capture_output=True, text=True, timeout=60)

        output = release / 'merged.zip'
        result = run_merge(output)
        assert result.returncode == 0, result.stderr + result.stdout
        assert output.read_bytes() == archive_data

        # Inject another writer exactly at the publication boundary. A failed
        # non-overwriting move must only clean our temporary file.
        race_script = release / 'merge-race.ps1'
        source = script.read_text(encoding='utf-8-sig')
        boundary = '    [IO.File]::Move($temporaryPath, $outputPath)'
        assert source.count(boundary) == 1
        race_script.write_text(source.replace(boundary,
            "    [IO.File]::WriteAllText($outputPath, 'concurrent output')\n" + boundary), encoding='utf-8-sig')
        race_output = release / 'concurrent.zip'
        result = run_merge(race_output, race_script)
        assert result.returncode != 0
        assert race_output.read_text(encoding='utf-8-sig') == 'concurrent output'
        assert not list(release.glob('.bvwa-merge-*.tmp'))

        for bad_name in ('..\\outside.bin', 'C:\\outside.bin',
                         archive_name + '.001:stream', 'nested/' + archive_name + '.001'):
            bad = dict(metadata)
            bad['parts'] = [dict(parts[0], name=bad_name), dict(parts[1])]
            inventory.write_text(json.dumps(bad), encoding='utf-8')
            rejected = release / ('rejected-' + str(abs(hash(bad_name))) + '.zip')
            result = run_merge(rejected)
            assert result.returncode != 0, bad_name
            assert not rejected.exists()

        inventory.write_text(json.dumps(metadata), encoding='utf-8')
        first = release / (archive_name + '.001')
        backup = first.read_bytes()
        outside = release / 'outside.bin'
        outside.write_bytes(backup)
        first.unlink()
        try:
            os.symlink(outside, first)
        except OSError:
            first.write_bytes(backup)
        else:
            rejected = release / 'linked.zip'
            result = run_merge(rejected)
            assert result.returncode != 0, 'Linked part was accepted'
            assert not rejected.exists()
            first.unlink()
            first.write_bytes(backup)

        metadata['archive'] = dict(metadata['archive'], sha256='f' * 64)
        inventory.write_text(json.dumps(metadata), encoding='utf-8')
        rejected = release / 'bad-final.zip'
        result = run_merge(rejected)
        assert result.returncode != 0
        assert not rejected.exists(), 'Incomplete output was retained after checksum failure'
        assert not list(release.glob('.bvwa-merge-*.tmp'))


test_catalog_binding_and_archive_fail_closed()
test_launcher_metadata_is_rendered_from_version_json()
test_portable_parts_stay_in_release_folder()
print('PASS: runtime catalog, launcher metadata and portable part path integrity')
