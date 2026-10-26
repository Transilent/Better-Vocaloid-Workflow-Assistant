"""Real adapter, restoration, merge and resize behavior in an isolated APP."""
import copy,json,math,os,sys
from pathlib import Path
APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))
assert os.environ.get('BVWA_TESTING')=='1' and APP.parent.name=='test-sandboxes', 'Use tests/run_tests.py isolation'
from PyQt5.QtCore import QEvent,QPoint,QPointF,Qt
from PyQt5.QtGui import QMouseEvent,QWheelEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication,QComboBox,QVBoxLayout,QWidget
from ui_components import choice,AdaptiveColumns,LyricsEdit,StyleTimeline
from ui_preferences import AppearancePanel,merge_appearance,normalize,save_appearance
from common import ROOT,atomic_json,workspace_state
from vocal_expression import SV_QUARTER,TempoMap
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

# Real offscreen interaction for the style timeline. Use the same TempoMap as
# export so a blick offset and a tempo change are checked at display geometry.
quarter=SV_QUARTER
timeline_project={
    'time':{'tempo':[{'position':0,'bpm':120},{'position':4*quarter,'bpm':60}]},
    'tracks':[
        {'name':'Lead','mainRef':{'blickOffset':quarter},
         'mainGroup':{'notes':[
             {'onset':2*quarter,'duration':2*quarter,'pitch':60,'lyrics':'a'},
             {'onset':4*quarter,'duration':quarter//4,'pitch':65,'lyrics':'b'}]},
         'parameters':{'pitchDelta':{'points':[0,0,quarter,80]}}},
        {'name':'Backing','mainRef':{'blickOffset':-quarter},
         'mainGroup':{'notes':[{'onset':0,'duration':quarter,'pitch':55,'lyrics':'c'}]}}]}
project_frozen=copy.deepcopy(timeline_project)
timeline=StyleTimeline();timeline.resize(720,320);timeline.show();application.processEvents()
selection_changes=[];timeline.selectionChanged.connect(selection_changes.append)
timeline.set_project(timeline_project,[0,1]);application.processEvents()
assert timeline_project==project_frozen,'Timeline must not mutate project input'
clock=TempoMap(timeline_project['time']['tempo'])
display_notes=timeline._canvas._notes
assert [(note['track_index'],note['pitch']) for note in display_notes]==[(0,60),(0,65),(1,55)]
assert abs(display_notes[0]['start']-clock.to_seconds(3*quarter))<1e-12
assert abs(display_notes[0]['end']-clock.to_seconds(5*quarter))<1e-12
assert abs(display_notes[1]['start']-clock.to_seconds(5*quarter))<1e-12
assert abs(display_notes[1]['end']-clock.to_seconds(5*quarter+quarter//4))<1e-12
assert abs(display_notes[2]['start']-clock.to_seconds(-quarter))<1e-12
assert display_notes[0]['color'].name()!=display_notes[2]['color'].name()
assert timeline._canvas.scene_bounds()[0]<0,'Negative project time must remain selectable'
assert abs(timeline.time_at_view_x(0)-timeline._canvas.x_to_time(timeline._view.horizontalScrollBar().value()))<1e-12
assert abs(timeline.view_x_for_time(display_notes[0]['start'])+timeline._view.horizontalScrollBar().value()-timeline._canvas.time_to_x(display_notes[0]['start']))<1e-12
short_width=(display_notes[1]['end']-display_notes[1]['start'])*timeline._canvas.pixels_per_second
assert abs(short_width-.25*timeline._canvas.pixels_per_second)<1e-12

# Piano keys and the second ruler are viewport overlays, not parts of the full
# canvas that disappear after vertical or horizontal scrolling.
timeline._center_vertical_on_notes();application.processEvents()
viewport=timeline._view.viewport();piano_header=timeline._piano_header;ruler_header=timeline._ruler_header
assert timeline._view.verticalScrollBar().value()>0
assert piano_header.isVisible() and ruler_header.isVisible()
assert piano_header.geometry().x()==0 and piano_header.height()==viewport.height()
assert ruler_header.geometry().x()==timeline._canvas.PIANO_WIDTH and ruler_header.height()==timeline._canvas.RULER_HEIGHT
piano_image=piano_header.grab().toImage();ruler_image=ruler_header.grab().toImage();viewport_image=viewport.grab().toImage()
assert not piano_image.isNull() and not ruler_image.isNull() and not viewport_image.isNull()
key_colors={piano_image.pixelColor(4,y).name() for y in range(timeline._canvas.RULER_HEIGHT,piano_image.height(),timeline._canvas.PITCH_HEIGHT)}
assert {'#171c22','#f8f7f1'}.issubset(key_colors),key_colors
assert viewport_image.pixelColor(10,100)==piano_image.pixelColor(10,100)
assert viewport_image.pixelColor(100,10)==ruler_image.pixelColor(28,10)
bar=timeline._view.horizontalScrollBar();bar.setValue(min(bar.maximum(),120));application.processEvents();ruler_header.grab();viewport_image=viewport.grab().toImage()
assert piano_header.geometry().x()==0 and ruler_header.geometry().x()==timeline._canvas.PIANO_WIDTH
assert ruler_header.visible_ticks and all(math.isfinite(value) for value in ruler_header.visible_ticks)
header_start=timeline.time_at_view_x(timeline._canvas.PIANO_WIDTH);header_end=timeline.time_at_view_x(viewport.width())
first_tick=ruler_header.visible_ticks[0]
assert header_start<=first_tick<=header_end
assert -1<=timeline.view_x_for_time(first_tick)-timeline._canvas.PIANO_WIDTH<=ruler_header.width()
assert viewport_image.pixelColor(10,100)==piano_header.grab().toImage().pixelColor(10,100)
assert viewport_image.pixelColor(100,10)==ruler_header.grab().toImage().pixelColor(28,10)
bar.setValue(0);application.processEvents()

regions=[{'start':.8,'end':2.2,'preset':'gentle','strength':.6},
         {'start':2.6,'end':3.1,'label':'A','preset':'bold'},
         {'start':3.3,'end':3.8,'preset':'sad','strength':.6},
         {'start':20,'end':30,'label':'B','preset':'bold'}]
regions_frozen=copy.deepcopy(regions);timeline.set_regions(regions);application.processEvents()
assert regions==regions_frozen,'Timeline must not mutate region records'
assert [row['label'] for row in timeline._canvas._regions][0] in ('柔和','Gentle','やわらか')
assert timeline._canvas._regions[2]['label'] in ('悲伤','Sad','悲しみ')
assert timeline._canvas._regions[2]['color'].name()=='#8a5ba8'

# Custom labels must not bypass preset selection, including the first region
# and a different style immediately after a sad region.
labelled_regions=[{'start':.8,'end':1.2,'label':'Custom Sad','preset':'sad'},
                  {'start':1.3,'end':1.7,'label':'Custom Bright','preset':'bold'},
                  {'start':1.8,'end':2.2,'label':'Custom Sad Again','preset':'sad'},
                  {'start':2.3,'end':2.7,'label':'Unspecified Style'}]
labelled_frozen=copy.deepcopy(labelled_regions)
timeline.set_regions(labelled_regions);application.processEvents()
assert labelled_regions==labelled_frozen
shown=timeline._canvas._regions
assert [row['label'] for row in shown]==[row['label'] for row in labelled_regions]
assert shown[0]['color'].name()==shown[2]['color'].name()=='#8a5ba8'
assert shown[1]['color'].name()!='#8a5ba8','A labelled bright region inherited the preceding sad colour'
assert shown[3]['color'].name()!='#8a5ba8','A labelled region without a preset inherited the preceding sad colour'
timeline.set_regions(regions);application.processEvents()

timeline.set_selection(1.2,2.4);assert timeline.selection()==(1.2,2.4)
assert timeline.set_selection(2.4,1.2)==(1.2,2.4)
try:timeline.set_selection(float('nan'),2);raise AssertionError('non-finite selection accepted')
except ValueError:pass
assert timeline.selection()==(1.2,2.4)
clamped=timeline.set_selection(-999,999);assert clamped==timeline._canvas.scene_bounds()
timeline.set_project(timeline_project,[0,1]);assert timeline.selection() is None
assert selection_changes and selection_changes[-1] is None
assert not timeline._canvas._regions and timeline._canvas.scene_bounds()[1]<11,'New project retained stale region bounds'

# Dragging from later time to earlier time sorts both real-time endpoints.
left=round(timeline._canvas.time_to_x(2.4));right=round(timeline._canvas.time_to_x(.9));y=timeline._canvas.height()-100
QTest.mousePress(timeline._canvas,Qt.LeftButton,pos=QPoint(left,y));QApplication.sendEvent(timeline._canvas,QMouseEvent(QEvent.MouseMove,QPointF(right,y),Qt.NoButton,Qt.LeftButton,Qt.NoModifier));QTest.mouseRelease(timeline._canvas,Qt.LeftButton,pos=QPoint(right,y));application.processEvents()
assert timeline.selection() is not None and timeline.selection()[0]<timeline.selection()[1]
assert abs(timeline.selection()[0]-timeline._canvas.x_to_time(right))<1e-9
assert abs(timeline.selection()[1]-timeline._canvas.x_to_time(left))<1e-9
assert selection_changes[-1]==timeline.selection()

# Disabling leaves the current choice intact and a programmatic clear/Escape
# sends None through the sole public signal.
selection_before=timeline.selection();timeline.setEnabled(False)
QTest.mousePress(timeline._canvas,Qt.LeftButton,pos=QPoint(left,y));QApplication.sendEvent(timeline._canvas,QMouseEvent(QEvent.MouseMove,QPointF(right,y),Qt.NoButton,Qt.LeftButton,Qt.NoModifier));QTest.mouseRelease(timeline._canvas,Qt.LeftButton,pos=QPoint(right,y));application.processEvents()
assert timeline.selection()==selection_before
timeline.setEnabled(True);timeline.set_selection(.5,1.5);timeline._canvas.setFocus();QTest.keyClick(timeline._canvas,Qt.Key_Escape);application.processEvents()
assert timeline.selection() is None and selection_changes[-1] is None
timeline.set_selection(.5,1.5);timeline.clear_selection();assert timeline.selection() is None and selection_changes[-1] is None

# Zoom preserves the visible center time and selection seconds; scrolling only
# changes viewport position. Non-finite zoom requests are ignored.
timeline.set_selection(1.25,2.75);center_x=max(0,timeline._view.viewport().width()//2)
before_wheel=timeline.zoom_factor();wheel=QWheelEvent(QPointF(center_x,20),QPointF(center_x,20),QPoint(0,0),QPoint(0,120),Qt.NoButton,Qt.ControlModifier,Qt.NoScrollPhase,False)
QApplication.sendEvent(timeline._canvas,wheel);application.processEvents()
assert timeline.zoom_factor()>before_wheel,'Ctrl+wheel did not zoom'
before_center=timeline.time_at_view_x(center_x);timeline.set_zoom(2.0);application.processEvents()
assert timeline.selection()==(1.25,2.75)
assert abs(timeline.time_at_view_x(center_x)-before_center)<.03
assert math.isfinite(timeline.zoom_factor()) and math.isfinite(timeline._canvas.pixels_per_second)
bar=timeline._view.horizontalScrollBar();bar.setValue(min(bar.maximum(),max(bar.minimum(),bar.value()+80)));application.processEvents()
anchor=timeline.view_x_for_time(1.75)
assert abs(timeline.time_at_view_x(anchor)-1.75)<1e-8
assert timeline.selection()==(1.25,2.75)
old_zoom=timeline.zoom_factor();assert not timeline.set_zoom(float('nan')) and timeline.zoom_factor()==old_zoom

# A narrow parent keeps its layout stable; excess timeline width stays inside
# the scroll viewport and at least 160px remains available for drawing.
timeline.resize(320,240);application.processEvents()
assert timeline.width()<=320 and timeline.minimumSizeHint().width()<=320
assert timeline._view.viewport().height()>=160
assert timeline._view.horizontalScrollBar().maximum()>0
assert timeline._canvas.canvas_width()<=timeline._canvas.MAX_CANVAS_WIDTH
assert timeline._piano_header.geometry().height()==timeline._view.viewport().height()
assert timeline._ruler_header.geometry().width()==timeline._view.viewport().width()-timeline._canvas.PIANO_WIDTH

# A scrolled hour-long canvas still produces finite, real-second major ticks.
timeline.resize(720,320);application.processEvents()
vertical=timeline._view.verticalScrollBar()
before_pitch_center=vertical.value()+timeline._view.viewport().height()/2.
timeline.resize(720,500);application.processEvents()
after_pitch_center=vertical.value()+timeline._view.viewport().height()/2.
assert abs(before_pitch_center-after_pitch_center)<=1.,'Resizing moved the visible pitch centre'

long_project={'time':{'tempo':[{'position':0,'bpm':120}]},'tracks':[{'mainRef':{'blickOffset':0},'mainGroup':{'notes':[{'onset':0,'duration':7200*quarter,'pitch':60}]}}]}
timeline.resize(720,320);timeline.set_zoom(.125);timeline.set_project(long_project,[0]);application.processEvents();bar=timeline._view.horizontalScrollBar();bar.setValue(min(bar.maximum(),5000));application.processEvents()
visible_start=timeline.time_at_view_x(timeline._canvas.PIANO_WIDTH);visible_end=timeline.time_at_view_x(timeline._view.viewport().width())
ticks=timeline._canvas._major_ticks(visible_start,visible_end)
assert len(ticks)>=4 and all(math.isfinite(value) for value in ticks)
assert visible_start<ticks[0]<=ticks[1]<visible_end
assert abs((ticks[1]-ticks[0])-timeline._canvas._major_interval())<1e-9
timeline._center_vertical_on_notes();application.processEvents();timeline._ruler_header.grab();rendered=timeline._view.viewport().grab().toImage()
assert timeline._ruler_header.visible_ticks==ticks
assert not rendered.isNull() and rendered.width()==timeline._view.viewport().width()
assert timeline._piano_header.isVisible() and timeline._ruler_header.isVisible()
assert timeline._piano_header.geometry().x()==0 and timeline._ruler_header.geometry().x()==timeline._canvas.PIANO_WIDTH
assert timeline._ruler_header.visible_ticks and all(math.isfinite(value) for value in timeline._ruler_header.visible_ticks)
timeline.close();application.processEvents()
print('PASS: style timeline tempo/offset, sticky viewport ruler/keys, reverse drag, Ctrl-wheel/scroll seconds, immutable regions/project reset, disabled/Escape, long-axis ticks, and narrow layout')
