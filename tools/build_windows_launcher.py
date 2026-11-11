"""Build the small Windows GUI launcher and its original icon; no release archive."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ASSEMBLY_VERSION_TOKEN = '__VWA_ASSEMBLY_VERSION__'
INFORMATIONAL_VERSION_TOKEN = '__VWA_INFORMATIONAL_VERSION__'


def _version(version_file=None):
    path = Path(version_file) if version_file else ROOT / 'packaging/version.json'
    value = json.loads(path.read_text(encoding='utf-8')).get('version')
    if not isinstance(value, str) or not re.fullmatch(
            r'\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?', value):
        raise ValueError('Invalid application version for launcher metadata')
    return value


def assembly_version(version):
    """Map the application release to a numeric four-part assembly version."""
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?', version)
    if not match:
        raise ValueError('Invalid application version for launcher metadata')
    parts = [int(part) for part in match.groups()]
    if any(part > 65535 for part in parts):
        raise ValueError('Launcher assembly version component exceeds 65535')
    return '.'.join(str(part) for part in parts + [0])


def render_launcher_source(template, version):
    """Replace compile-time metadata tokens with version.json values."""
    if ASSEMBLY_VERSION_TOKEN not in template or INFORMATIONAL_VERSION_TOKEN not in template:
        raise ValueError('Launcher source lacks version metadata tokens')
    return (template.replace(ASSEMBLY_VERSION_TOKEN, assembly_version(version))
            .replace(INFORMATIONAL_VERSION_TOKEN, version))


def build():
    icon = ROOT / 'assets/app.ico'
    if not icon.is_file():
        raise FileNotFoundError('The checked-in multi-resolution assets/app.ico is required.')
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    if not compiler.is_file():
        raise FileNotFoundError('Windows .NET Framework C# compiler is required for the launcher build.')
    version = _version()
    template = (ROOT / 'tools/windows/Launcher.cs').read_text(encoding='utf-8')
    generated = render_launcher_source(template, version)
    temporary_root = ROOT / 'work'
    temporary_root.mkdir(exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode='w', encoding='utf-8', suffix='.cs', prefix='launcher-',
        dir=temporary_root, delete=False)
    try:
        handle.write(generated)
        handle.close()
        subprocess.run([
            str(compiler), '/nologo', '/target:winexe', '/platform:x64', '/optimize+',
            '/utf8output', '/codepage:65001', '/reference:System.Windows.Forms.dll',
            '/win32icon:' + str(icon), '/out:' + str(ROOT / 'VWA.exe'), handle.name,
        ], check=True)
    finally:
        if not handle.file.closed:
            handle.close()
        Path(handle.name).unlink(missing_ok=True)
    print('Built VWA.exe with version %s metadata and a multi-resolution app icon. No release archive was created.' % version)


if __name__ == '__main__':
    build()
