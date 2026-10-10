"""Build the small Windows GUI launcher and its original icon; no release archive."""
import os
from pathlib import Path
import subprocess
import struct

ROOT = Path(__file__).resolve().parents[1]


def build():
    from PyQt5.QtCore import Qt, QBuffer, QByteArray, QIODevice
    from PyQt5.QtGui import QImage, QPainter
    from PyQt5.QtSvg import QSvgRenderer
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    canvas = QImage(256, 256, QImage.Format_ARGB32)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(str(ROOT / 'assets/app.svg')).render(painter)
    painter.end()
    canvas.save(str(ROOT / 'assets/app.png'))
    images = []
    for size in (16, 24, 32, 48, 64, 128, 256):
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        canvas.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(buffer, 'PNG')
        images.append((size, bytes(data)))
    offset = 6 + 16 * len(images)
    directory = bytearray(struct.pack('<HHH', 0, 1, len(images)))
    for size, data in images:
        directory.extend(struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    (ROOT / 'assets/app.ico').write_bytes(directory + b''.join(data for size, data in images))
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    if not compiler.is_file():
        raise FileNotFoundError('Windows .NET Framework C# compiler is required for the launcher build.')
    subprocess.run([str(compiler), '/nologo', '/target:winexe', '/platform:x64', '/optimize+',
                    '/utf8output', '/codepage:65001', '/reference:System.Windows.Forms.dll',
                    '/win32icon:' + str(ROOT / 'assets/app.ico'), '/out:' + str(ROOT / 'BVWA.exe'),
                    str(ROOT / 'tools/windows/Launcher.cs')], check=True)
    print('Built BVWA.exe and multi-resolution app icon. No release archive was created.')


if __name__ == '__main__':
    build()
