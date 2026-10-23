"""Default artwork is one cached background scene; no character preference."""
import hashlib,json,os,sys
from pathlib import Path
APP=Path(__file__).resolve().parents[1];sys.path.insert(0,str(APP))
assert os.environ.get('BVWA_TESTING')=='1' and APP.parent.name=='test-sandboxes','Use tests/run_tests.py'
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage,QPixmap
from PyQt5.QtWidgets import QApplication,QCheckBox,QWidget
from app import Window
from common import atomic_json
from desktop_theme import apply_theme
from ui_materials import MaterialBackdrop

app=QApplication([])
# An obsolete illustration switch must neither hide the new default background
# nor be deleted from a user's older workspace merely by opening the window.
workspace=APP/'cache/workspace.json'
atomic_json(workspace,{'appearance':{'decoration_enabled':False,'material':'glass'},'preserved_future_key':7})
before=workspace.read_bytes()
window=Window();window.show()
def settle():
    for _ in range(4):app.processEvents()
def digest(pixmap):
    img=pixmap.toImage().convertToFormat(QImage.Format_RGBA8888)
    pixels=img.bits();pixels.setsize(img.byteCount())
    return hashlib.sha256(bytes(pixels)).hexdigest()
settle();panel=window.appearance_panel;backdrop=window._vwa_backdrop
assert not hasattr(window,'sidebar_decoration') and not hasattr(panel,'decoration')
assert all('人物' not in check.text() for check in panel.findChildren(QCheckBox))
assert backdrop.testAttribute(Qt.WA_TransparentForMouseEvents) and backdrop.focusPolicy()==Qt.NoFocus
assert not backdrop._character.isNull() and not backdrop._texture.isNull()
image=QImage(str(APP/'assets/headphone-character.png'))
assert image.hasAlphaChannel() and image.pixelColor(0,0).alpha()==0
portrait,texture=backdrop._character,backdrop._texture

# Real rendered pixels show both asset layers participate in the scene.
apply_theme(window,'light');settle();complete=digest(backdrop.grab())
key=backdrop._pixmap.cacheKey();backdrop.update();settle();assert backdrop._pixmap.cacheKey()==key
backdrop._character=QPixmap();backdrop.configure('light',window._vwa_appearance);settle()
without_character=digest(backdrop.grab());assert without_character!=complete
backdrop._texture=QPixmap();backdrop.configure('light',window._vwa_appearance);settle()
assert digest(backdrop.grab())!=without_character
backdrop._character,backdrop._texture=portrait,texture

# Character stays in the background on a short window: no layout slot or control.
window.resize(1000,680);apply_theme(window,'dark');settle()
assert backdrop._character.cacheKey()==portrait.cacheKey()
window.resize(1600,1000);apply_theme(window,'light');settle()
assert backdrop._character_rect().width()>=300 and backdrop._character_rect().bottom()==window.height()
window._vwa_appearance['material']='solid';apply_theme(window,'light');settle()
solid=digest(backdrop.grab());backdrop._character=QPixmap();backdrop._texture=QPixmap()
backdrop.configure('light',window._vwa_appearance);settle();assert digest(backdrop.grab())==solid
backdrop._character,backdrop._texture=portrait,texture;window._vwa_appearance['material']='glass'

out=APP/'work/test-results';out.mkdir(parents=True,exist_ok=True)
for mode in ('light','dark'):
    apply_theme(window,mode);settle()
    for index,name in ((0,'preparation'),(8,'video')):
        window.navigate(index);settle();assert window.grab().save(str(out/f'art-{name}-{mode}.png'))
window.resize(2512,1352);apply_theme(window,'light');settle()
large_metrics={}
for index,name in ((0,'preparation'),(9,'tuning')):
    window.navigate(index);settle()
    assert window.grab().save(str(out/f'art-{name}-large.png'))
    from PyQt5.QtWidgets import QScrollArea
    areas=window.pages.currentWidget().findChildren(QScrollArea)
    large_metrics[name]={'size':[window.width(),window.height()],
        'visible_scroll_ranges':[area.verticalScrollBar().maximum() for area in areas if area.isVisible()]}
window.navigate(0);settle()
viewport=window.step_area.parentWidget()
large_metrics['preparation']['subtitle_entry_inside']=viewport.rect().contains(
    viewport.mapFromGlobal(window.subtitle_button.mapToGlobal(window.subtitle_button.rect().bottomRight())))
(out/'art-large-layout.json').write_text(json.dumps(large_metrics,ensure_ascii=False,indent=2),encoding='utf-8')
assert workspace.read_bytes()==before
catalog=json.loads((APP/'packaging/source-files.json').read_text(encoding='utf-8'))['files']
portable=json.loads((APP/'packaging/portable-files.json').read_text(encoding='utf-8'))['entries']
for name in ('headphone-character.png','moonlight-background.png'):
    assert 'assets/'+name in catalog and any(row['file']=='assets/'+name for row in portable)
owner=QWidget();owner.resize(420,1000)
fallback=MaterialBackdrop(owner,APP/'work/missing-texture.png',APP/'work/missing-character.png')
owner.show();fallback.configure('light',{'material':'glass'});settle()
assert fallback._texture.isNull() and fallback._character.isNull() and not fallback.grab().isNull()
window.close();owner.close();settle();assert workspace.read_bytes()==before
print('PASS: enlarged default background artwork, no character switch/widget, legacy-state preservation, cached real layers, solid/missing fallback and manifests')
