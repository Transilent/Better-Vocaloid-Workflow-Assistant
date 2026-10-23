"""Real adapter, restoration, merge and resize behavior in an isolated APP."""
import json,os,sys
from pathlib import Path
APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))
assert os.environ.get('BVWA_TESTING')=='1' and APP.parent.name=='test-sandboxes', 'Use tests/run_tests.py isolation'
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication,QComboBox,QVBoxLayout,QWidget
from ui_components import choice,AdaptiveColumns,LyricsEdit
from ui_preferences import AppearancePanel,merge_appearance,normalize,save_appearance
from common import ROOT,atomic_json,workspace_state
application=QApplication([])
owner=QWidget();layout=QVBoxLayout(owner)
combo=QComboBox();combo.addItem('中文','zh');combo.addItem('日语','ja')
view=choice(combo,columns=2,compact=True);layout.addWidget(view)
owner.resize(650,300);owner.show();application.processEvents()
changes=[];combo.currentIndexChanged.connect(changes.append)
QTest.mouseClick(view.buttons[1],Qt.LeftButton);application.processEvents()
assert combo.currentData()=='ja' and changes==[1]
combo.setCurrentIndex(0);assert view.buttons[0].isChecked()
view.buttons[0].setFocus()
QTest.keyClick(view.buttons[0],Qt.Key_Right);application.processEvents()
assert combo.currentData()=='ja', 'Native arrow-key selection must update the sole value owner'
QTest.keyClick(view.buttons[0],Qt.Key_Space);application.processEvents()
assert combo.currentData()=='zh'
combo.addItem('自定义 · 61%',.61);combo.setCurrentIndex(2)
assert view.buttons[2].isChecked() and combo.currentData()==.61
combo.model().item(2).setEnabled(False)
assert view.buttons[2].isChecked() and not view.buttons[2].isEnabled()
view.choose(2);assert combo.currentIndex()==2
combo.setCurrentIndex(-1);assert not any(button.isChecked() for button in view.buttons)
combo.setEnabled(False);assert not any(button.isEnabled() for button in view.buttons)
combo.setEnabled(True);assert view.buttons[0].isEnabled() and not view.buttons[2].isEnabled()
combo.removeItem(2);assert len(view.buttons)==2
combo.clear();assert not view.buttons
combo.addItem('恢复','restore');assert len(view.buttons)==1 and view.buttons[0].isChecked()
lyrics=LyricsEdit();lyrics.setText('第一行\n第二行');assert lyrics.text()=='第一行\n第二行'
left,right=QWidget(),QWidget();columns=AdaptiveColumns(left,right,breakpoint=700)
columns.resize(950,300);columns.show();application.processEvents();assert not columns._stacked
columns.resize(500,300);application.processEvents();assert columns._stacked
old={'tuning':{'preferences':{'style_strength':.61},'session':'keep/session.json'},
     'output_directory':'keep-output','processing':{'preset':'keep'},'appearance':{'future_key':7}}
atomic_json(ROOT/'cache/workspace.json',old)
save_appearance({'ui_scale':115,'material':'solid','decoration_enabled':False,'reduce_motion':True})
saved=workspace_state();assert saved['tuning']==old['tuning'] and saved['output_directory']=='keep-output'
assert saved['processing']==old['processing'] and saved['appearance']['future_key']==7
assert saved['appearance']['ui_scale']==115
assert merge_appearance(saved,{'material':'glass'})['appearance']['ui_scale']==115
assert normalize({'ui_scale':True,'material':'unknown'})['ui_scale']==100
assert 'future_key' in merge_appearance(saved,normalize({}))['appearance']
owner.theme_picker=QComboBox();owner.theme_picker.addItem('light','light');owner.theme_picker.addItem('dark','dark')
before=(ROOT/'cache/workspace.json').read_bytes()
panel=AppearancePanel(owner);application.processEvents()
assert (ROOT/'cache/workspace.json').read_bytes()==before, 'Constructing appearance UI must not write workspace'
panel.material.setCurrentIndex(panel.material.findData('glass'));application.processEvents()
assert (ROOT/'cache/workspace.json').read_bytes()==before, 'BVWA_TESTING UI writes remain guarded'
assert owner._vwa_appearance['material']=='glass'
backdrop=owner._vwa_backdrop;owner.update();application.processEvents();key=backdrop._pixmap.cacheKey()
owner.update();application.processEvents();assert backdrop._pixmap.cacheKey()==key
owner.close();columns.close();panel.close();application.processEvents()
print('PASS: real choice events/model changes/unset/disabled, multiline compatibility, responsive reflow, appearance merge/init guards and cached material')
