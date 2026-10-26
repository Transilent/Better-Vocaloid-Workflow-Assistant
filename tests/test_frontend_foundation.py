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
assert timeline.pitch_height()>=36,'The default piano roll must draw readable note rows'
assert math.isclose(timeline.zoom_factor(),3.25,rel_tol=1e-9,abs_tol=1e-9)
assert timeline._canvas.pixels_per_second>=240,'The default timeline must leave enough horizontal detail for notes'
selection_changes=[];timeline.selectionChanged.connect(selection_changes.append)
timeline.set_project(timeline_project,[0,1]);application.processEvents()
def timeline_drag(widget,start,end,modifiers=Qt.NoModifier):
    QTest.mousePress(widget,Qt.LeftButton,modifier=modifiers,pos=start)
    QApplication.sendEvent(widget,QMouseEvent(QEvent.MouseMove,QPointF(end),Qt.NoButton,Qt.LeftButton,modifiers))
    QTest.mouseRelease(widget,Qt.LeftButton,modifier=modifiers,pos=end)
    application.processEvents()
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
key_colors={piano_image.pixelColor(4,y).name() for y in range(timeline._canvas.RULER_HEIGHT,piano_image.height(),int(round(timeline.pitch_height())))}
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
bar=timeline._view.horizontalScrollBar();bar.setValue(min(bar.maximum(),max(bar.minimum(),round(timeline._canvas.time_to_x(2.0)-center_x))));application.processEvents()
before_wheel=timeline.zoom_factor();wheel_anchor=timeline.time_at_view_x(center_x)
canvas_x=bar.value()+center_x
wheel=QWheelEvent(QPointF(canvas_x,20),QPointF(timeline._canvas.mapToGlobal(QPoint(canvas_x,20))),QPoint(0,0),QPoint(0,120),Qt.NoButton,Qt.ControlModifier,Qt.NoScrollPhase,False)
QApplication.sendEvent(timeline._canvas,wheel);application.processEvents()
assert timeline.zoom_factor()>before_wheel,'Ctrl+wheel did not zoom'
assert abs(timeline.time_at_view_x(center_x)-wheel_anchor)<=max(1e-6,1.5/timeline._canvas.pixels_per_second),'Ctrl+wheel moved the time under the pointer'
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

# The sticky style lane must edit through an explicit request while preserving
# both the caller's region records and the committed preview-independent view.
editor_project={'time':{'tempo':[{'position':0,'bpm':120}]},'tracks':[{'mainRef':{'blickOffset':0},'mainGroup':{'notes':[{'onset':0,'duration':24*quarter,'pitch':60},{'onset':8*quarter,'duration':2*quarter,'pitch':64}]}}]}
editor_project_frozen=copy.deepcopy(editor_project)
editor_regions=[{'start':2.4,'end':3.4,'preset':'gentle'},{'start':4.2,'end':7.0,'preset':'sad','strength':.6},{'start':7.8,'end':8.8,'preset':'bold'}]
editor_regions_frozen=copy.deepcopy(editor_regions)
timeline.set_project(editor_project,[0]);timeline.set_regions(editor_regions);timeline.set_snap_enabled(False);timeline.set_zoom(.9);application.processEvents()
assert timeline._canvas.pitch_to_y(60)-timeline._canvas.pitch_to_y(61)>=36,'Piano rows are too small to read and target'
bar=timeline._view.horizontalScrollBar();piano_width=timeline._canvas.PIANO_WIDTH;header=timeline._ruler_header;lane_y=timeline._canvas.RULER_HEIGHT-6
bar.setValue(min(bar.maximum(),max(bar.minimum(),round(timeline._canvas.time_to_x(5.6)-piano_width-300))));application.processEvents()
def ruler_x(seconds):
    return int(round(timeline._canvas.time_to_x(seconds)-bar.value()-piano_width))
region_selected=[];region_edits=[];region_deletes=[]
timeline.regionSelected.connect(region_selected.append);timeline.regionEditRequested.connect(lambda index,start,end:region_edits.append((index,start,end)));timeline.regionDeleteRequested.connect(region_deletes.append)
target_start,target_end=editor_regions[1]['start'],editor_regions[1]['end']
target_x=ruler_x((target_start+target_end)/2)
assert 0<target_x<header.width(),'Target style region is outside the visible lane'
timeline.set_active_region(0);assert timeline._canvas._active_region==0
timeline.set_active_region(99);assert timeline._canvas._active_region==-1
QTest.mouseClick(header,Qt.LeftButton,pos=QPoint(target_x,lane_y));application.processEvents()
assert region_selected==[1] and timeline._canvas._active_region==1
assert not region_edits and editor_regions==editor_regions_frozen,'A region click selected but also edited caller data'

# A sub-two-pixel press/move/release stays a click, including while snapping is
# on, so selecting a region cannot silently nudge it onto a nearby note edge.
timeline.set_snap_enabled(True);region_edits.clear();QTest.mousePress(header,Qt.LeftButton,pos=QPoint(target_x,lane_y));QApplication.sendEvent(header,QMouseEvent(QEvent.MouseMove,QPointF(target_x+1,lane_y),Qt.NoButton,Qt.LeftButton,Qt.NoModifier));QTest.mouseRelease(header,Qt.LeftButton,pos=QPoint(target_x+1,lane_y));application.processEvents()
assert not region_edits and editor_regions==editor_regions_frozen,'A click with snap enabled changed a region'
timeline.set_snap_enabled(False)

move_start=ruler_x((target_start+target_end)/2);move_end=move_start+37
move_shift=(timeline._canvas.x_to_time(bar.value()+move_end+piano_width)-timeline._canvas.x_to_time(bar.value()+move_start+piano_width))
timeline_drag(header,QPoint(move_start,lane_y),QPoint(move_end,lane_y))
assert len(region_edits)==1 and region_edits[-1][0]==1
assert abs((region_edits[-1][2]-region_edits[-1][1])-(target_end-target_start))<1e-9,'Region move changed duration'
assert abs(region_edits[-1][1]-(target_start+move_shift))<1e-8
assert editor_regions==editor_regions_frozen and timeline._canvas._region_preview is None

# Zooming during a real drag cancels its uncommitted preview. The following
# release must neither emit an edit nor mutate the external region list.
region_edits.clear();QTest.mousePress(header,Qt.LeftButton,pos=QPoint(target_x,lane_y));QApplication.sendEvent(header,QMouseEvent(QEvent.MouseMove,QPointF(target_x+30,lane_y),Qt.NoButton,Qt.LeftButton,Qt.NoModifier));application.processEvents();assert timeline._canvas._region_preview is not None
timeline.set_zoom(1.1);application.processEvents();assert timeline._canvas._region_preview is None and timeline._region_gesture is None
QTest.mouseRelease(header,Qt.LeftButton,pos=QPoint(target_x+30,lane_y));application.processEvents();assert not region_edits and editor_regions==editor_regions_frozen
timeline.set_zoom(.9);bar.setValue(min(bar.maximum(),max(bar.minimum(),round(timeline._canvas.time_to_x(5.6)-piano_width-300))));application.processEvents();target_x=ruler_x((target_start+target_end)/2)

region_edits.clear();timeline_drag(header,QPoint(ruler_x(target_start),lane_y),QPoint(ruler_x(3.0),lane_y))
assert len(region_edits)==1 and abs(region_edits[-1][1]-editor_regions[0]['end'])<1e-9 and abs(region_edits[-1][2]-target_end)<1e-9,'Left resize crossed its previous neighbour'
region_edits.clear();timeline_drag(header,QPoint(ruler_x(target_end),lane_y),QPoint(ruler_x(9.2),lane_y))
assert len(region_edits)==1 and abs(region_edits[-1][1]-target_start)<1e-9 and abs(region_edits[-1][2]-editor_regions[2]['start'])<1e-9,'Right resize crossed its next neighbour'
region_edits.clear();timeline_drag(header,QPoint(ruler_x(target_start),lane_y),QPoint(ruler_x(target_end-.001),lane_y))
assert len(region_edits)==1 and abs(region_edits[-1][2]-region_edits[-1][1]-.025)<1e-9,'Left resize ignored the minimum duration'
region_edits.clear();timeline_drag(header,QPoint(ruler_x(target_end),lane_y),QPoint(ruler_x(target_start+.001),lane_y))
assert len(region_edits)==1 and abs(region_edits[-1][2]-region_edits[-1][1]-.025)<1e-9,'Right resize ignored the minimum duration'
assert editor_regions==editor_regions_frozen and timeline._canvas._region_preview is None

# The upper seconds ruler and lower blank lanes start a range without selecting
# or changing a style region; both report and clear the active region first.
timeline.set_selection(.5,1.0);timeline.set_active_region(1);region_selected.clear();QTest.mouseClick(header,Qt.LeftButton,pos=QPoint(ruler_x(4.8),8));application.processEvents()
assert region_selected==[-1] and timeline._canvas._active_region==-1 and timeline.selection()==(.5,1.0),'A blank ruler click did not clear the active region'
region_edits.clear();region_selected.clear();selection_changes.clear();upper_start=ruler_x(4.8);upper_end=upper_start+42
timeline_drag(header,QPoint(upper_start,8),QPoint(upper_end,8))
expected_selection=sorted((timeline._canvas.x_to_time(bar.value()+piano_width+upper_start),timeline._canvas.x_to_time(bar.value()+piano_width+upper_end)))
assert all(abs(a-b)<1e-9 for a,b in zip(timeline.selection(),expected_selection))
assert region_selected==[-1] and timeline._canvas._active_region==-1 and not region_edits and editor_regions==editor_regions_frozen
blank_start=ruler_x(3.55);blank_end=ruler_x(3.95);timeline.set_active_region(1);region_selected.clear();selection_changes.clear()
timeline_drag(header,QPoint(blank_start,lane_y),QPoint(blank_end,lane_y))
expected_selection=sorted((timeline._canvas.x_to_time(bar.value()+piano_width+blank_start),timeline._canvas.x_to_time(bar.value()+piano_width+blank_end)))
assert all(abs(a-b)<1e-9 for a,b in zip(timeline.selection(),expected_selection))
assert region_selected==[-1] and timeline._canvas._active_region==-1 and not region_edits and editor_regions==editor_regions_frozen

region_edits.clear();timeline.set_selection(1.0,2.0);esc_start=ruler_x((target_start+target_end)/2)
QTest.mousePress(header,Qt.LeftButton,pos=QPoint(esc_start,lane_y));QApplication.sendEvent(header,QMouseEvent(QEvent.MouseMove,QPointF(esc_start+30,lane_y),Qt.NoButton,Qt.LeftButton,Qt.NoModifier));application.processEvents();assert timeline._canvas._region_preview is not None
QTest.keyClick(header,Qt.Key_Escape);application.processEvents();assert timeline._canvas._region_preview is None and timeline.selection() is None
QTest.mouseRelease(header,Qt.LeftButton,pos=QPoint(esc_start+30,lane_y));application.processEvents()
assert not region_edits,'Escape did not cancel a pending region edit'

region_deletes.clear();QTest.mouseClick(header,Qt.LeftButton,pos=QPoint(target_x,lane_y));QTest.keyClick(header,Qt.Key_Delete);application.processEvents()
assert region_deletes==[1] and timeline._canvas._active_region==1
selected_before_disable=len(region_selected);edits_before_disable=len(region_edits);deletes_before_disable=len(region_deletes)
timeline.setEnabled(False);timeline_drag(header,QPoint(target_x,lane_y),QPoint(target_x+30,lane_y));QTest.mouseClick(header,Qt.LeftButton,pos=QPoint(target_x,lane_y));QTest.keyClick(header,Qt.Key_Delete);application.processEvents()
assert len(region_selected)==selected_before_disable and len(region_edits)==edits_before_disable and len(region_deletes)==deletes_before_disable,'Disabled timeline still accepted region input'
timeline.setEnabled(True);application.processEvents()

region_edits.clear();timeline.set_zoom(1.7);bar.setValue(min(bar.maximum(),max(bar.minimum(),round(timeline._canvas.time_to_x(5.6)-piano_width-280))));application.processEvents()
scrolled_start=ruler_x((target_start+target_end)/2);scrolled_end=scrolled_start+43
move_shift=(timeline._canvas.x_to_time(bar.value()+scrolled_end+piano_width)-timeline._canvas.x_to_time(bar.value()+scrolled_start+piano_width))
assert 0<scrolled_start<scrolled_end<header.width()
timeline_drag(header,QPoint(scrolled_start,lane_y),QPoint(scrolled_end,lane_y))
assert len(region_edits)==1 and abs(region_edits[-1][1]-(target_start+move_shift))<1e-8,'Scrolled/zoomed ruler x did not map to real seconds'
wheel_x=ruler_x(5.6);wheel_view_x=wheel_x+piano_width;header_anchor=timeline.time_at_view_x(wheel_view_x);zoom_before=timeline.zoom_factor()
header_wheel=QWheelEvent(QPointF(wheel_x,10),QPointF(wheel_x,10),QPoint(0,0),QPoint(0,120),Qt.NoButton,Qt.ControlModifier,Qt.NoScrollPhase,False)
QApplication.sendEvent(header,header_wheel);application.processEvents()
assert timeline.zoom_factor()>zoom_before and abs(timeline.time_at_view_x(wheel_view_x)-header_anchor)<=max(1e-6,1.5/timeline._canvas.pixels_per_second),'Ruler Ctrl+wheel lost the pointer time'
bar.setValue((bar.minimum()+bar.maximum())//2);shift_before=bar.value();shift_wheel=QWheelEvent(QPointF(80,10),QPointF(80,10),QPoint(0,0),QPoint(0,120),Qt.NoButton,Qt.ShiftModifier,Qt.NoScrollPhase,False)
QApplication.sendEvent(header,shift_wheel);application.processEvents();assert bar.value()==shift_before-120

bar.setValue((bar.minimum()+bar.maximum())//2);vertical=timeline._view.verticalScrollBar();vertical.setValue((vertical.minimum()+vertical.maximum())//2);pan_h,pan_v=bar.value(),vertical.value();pan_start=QPoint(bar.value()+200,vertical.value()+120);pan_end=pan_start+QPoint(60,30)
# Offscreen QTest.mouseMove does not deliver a native move event on Windows.
# Supply the same pointer's screen position while the middle button is held.
pan_global=timeline._canvas.mapToGlobal(pan_start)
QTest.mousePress(timeline._canvas,Qt.MiddleButton,pos=pan_start)
QApplication.sendEvent(timeline._canvas,QMouseEvent(QEvent.MouseMove,QPointF(pan_end),QPointF(pan_global+QPoint(60,30)),Qt.NoButton,Qt.MiddleButton,Qt.NoModifier))
QTest.mouseRelease(timeline._canvas,Qt.MiddleButton,pos=pan_end);application.processEvents()
assert bar.value()<pan_h and vertical.value()<pan_v,'Middle-button drag did not pan both axes'
region_edits.clear();assert editor_project==editor_project_frozen and editor_regions==editor_regions_frozen

# Fit uses the real toolbar button; selecting a range and zooming to it keeps
# both endpoints visible and leaves the selected seconds unchanged.
timeline.set_project(editor_project,[0]);timeline.set_regions(editor_regions);application.processEvents();timeline.set_zoom(2.5);bar.setValue(bar.maximum());application.processEvents()
QTest.mouseClick(timeline.fit_button,Qt.LeftButton);application.processEvents();scene_start,scene_end=timeline._canvas.scene_bounds()
assert bar.value()==bar.minimum() and timeline.time_at_view_x(piano_width)<=scene_start+1e-6 and timeline.time_at_view_x(timeline._view.viewport().width())>=scene_end-1e-6,'Fit did not expose the whole project'
timeline.set_selection(4.5,6.0);assert timeline.zoom_selection_button.isEnabled();timeline.set_zoom(.5);bar.setValue(bar.maximum());application.processEvents();selection_zoom=timeline.zoom_factor()
QTest.mouseClick(timeline.zoom_selection_button,Qt.LeftButton);application.processEvents()
assert timeline.zoom_factor()>selection_zoom and timeline.selection()==(4.5,6.0)
assert piano_width-2<=timeline.view_x_for_time(4.5)<timeline.view_x_for_time(6.0)<=timeline._view.viewport().width()+2,'Selection zoom left an endpoint outside the view'

# Snapping is toggled by the toolbar and a real selection drag honors the
# nearest note edge, the 9-pixel radius, and Alt's temporary bypass.
snap_project={'time':{'tempo':[{'position':0,'bpm':120}]},'tracks':[{'mainRef':{'blickOffset':0},'mainGroup':{'notes':[{'onset':0,'duration':2*quarter,'pitch':60},{'onset':4*quarter,'duration':2*quarter,'pitch':64},{'onset':12*quarter,'duration':2*quarter,'pitch':67}]}}]}
snap_project_frozen=copy.deepcopy(snap_project);timeline.set_project(snap_project,[0]);timeline.set_zoom(1.0);timeline.set_snap_enabled(False);bar.setValue(bar.minimum());application.processEvents()
snap_edge=sorted({value for note in timeline._canvas._notes for value in (note['start'],note['end'])})[2];pps=timeline._canvas.pixels_per_second
QTest.mouseClick(timeline.snap_button,Qt.LeftButton);application.processEvents();assert timeline.snap_enabled() and timeline.snap_button.isChecked()
assert abs(timeline._canvas.snap_time(snap_edge+8/pps)-snap_edge)<1e-9
assert abs(timeline._canvas.snap_time(snap_edge+10/pps)-(snap_edge+10/pps))<1e-9
snap_y=round(timeline._canvas.pitch_to_y(64)+5);drag_start=QPoint(round(timeline._canvas.time_to_x(.5)),snap_y);snap_target=QPoint(round(timeline._canvas.time_to_x(snap_edge))+5,snap_y)
timeline_drag(timeline._canvas,drag_start,snap_target);assert abs(timeline.selection()[1]-snap_edge)<1e-9,'Native drag did not snap to a nearby note edge'
timeline.clear_selection();timeline_drag(timeline._canvas,drag_start,snap_target,Qt.AltModifier);assert abs(timeline.selection()[1]-timeline._canvas.x_to_time(snap_target.x()))<1e-8 and abs(timeline.selection()[1]-snap_edge)>1e-3,'Alt did not bypass snapping'
timeline.clear_selection();snap_target=QPoint(round(timeline._canvas.time_to_x(snap_edge))+11,snap_y);timeline_drag(timeline._canvas,drag_start,snap_target);assert abs(timeline.selection()[1]-timeline._canvas.x_to_time(snap_target.x()))<1e-8,'Snap exceeded its pixel radius'
timeline.set_snap_enabled(False);assert not timeline.snap_enabled() and not timeline.snap_button.isChecked();assert snap_project==snap_project_frozen

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
print('PASS: style timeline tempo/offset, sticky headers, native selection and style-lane gestures, immutable requests, fit/selection zoom/snap, panning/scroll seconds, disabled/Escape, long-axis ticks, and narrow layout')

# Readable vertical/horizontal controls preserve view and model state. The
# project is long enough that default pps is not capped by MAX_CANVAS_WIDTH.
readable_project={'time':{'tempo':[{'position':0,'bpm':120}]},'tracks':[{'mainRef':{'blickOffset':0},'mainGroup':{'notes':[{'onset':0,'duration':16*quarter,'pitch':60},{'onset':0,'duration':16*quarter,'pitch':72}]}}]}
readable_regions=copy.deepcopy(editor_regions)
readable=StyleTimeline();readable.resize(720,360);readable.show();application.processEvents()
readable.set_project(readable_project,[0]);readable.set_regions(readable_regions);application.processEvents()
project_snapshot=copy.deepcopy(readable_project);regions_snapshot=copy.deepcopy(readable_regions)
readable.set_selection(1.0,2.0);selection_snapshot=readable.selection()
assert readable.pitch_height()>=36 and readable._canvas.pixels_per_second>=240
assert math.isclose(readable.zoom_factor(),3.25,rel_tol=1e-9,abs_tol=1e-9)

def pitch_center(view):
    canvas=view._canvas
    y=view._view.verticalScrollBar().value()+view._view.viewport().height()/2.
    return 127.-(y-canvas.RULER_HEIGHT)/view.pitch_height()

before_pitch_center=pitch_center(readable)
assert readable.set_pitch_height(1);application.processEvents()
assert math.isclose(readable.pitch_height(),32,rel_tol=1e-9,abs_tol=1e-9)
assert abs(pitch_center(readable)-before_pitch_center)<=.25,'Pitch resize changed the visible centre pitch'
assert readable.set_pitch_height(1000);application.processEvents()
assert math.isclose(readable.pitch_height(),64,rel_tol=1e-9,abs_tol=1e-9)
assert abs(pitch_center(readable)-before_pitch_center)<=.25,'Maximum pitch height changed the visible centre pitch'
canvas=readable._canvas
state_before=(readable.pitch_height(),canvas.pitch_to_y(60),canvas.time_to_x(5),
              readable.selection(),canvas.scene_bounds(),readable._view.verticalScrollBar().value(),
              [(note['track_index'],note['start'],note['end'],note['pitch'],note['color'].name(),note['lyric']) for note in canvas._notes],
              [(row['index'],row['start'],row['end'],row['label']) for row in canvas._regions],
              copy.deepcopy(readable_project),copy.deepcopy(readable_regions))
for invalid in (float('nan'),float('inf'),'bad',None):
    try:
        readable.set_pitch_height(invalid)
    except (TypeError,ValueError,OverflowError):
        pass
    state_after=(readable.pitch_height(),canvas.pitch_to_y(60),canvas.time_to_x(5),
                 readable.selection(),canvas.scene_bounds(),readable._view.verticalScrollBar().value(),
                 [(note['track_index'],note['start'],note['end'],note['pitch'],note['color'].name(),note['lyric']) for note in canvas._notes],
                 [(row['index'],row['start'],row['end'],row['label']) for row in canvas._regions],
                 copy.deepcopy(readable_project),copy.deepcopy(readable_regions))
    assert state_after==state_before,(invalid,state_after,state_before)

readable.set_pitch_height(44);readable.set_zoom(.3);application.processEvents()
vertical=readable._view.verticalScrollBar();vertical.setValue(vertical.minimum())
readable.readable_zoom();application.processEvents()
assert math.isclose(readable.zoom_factor(),3.25,rel_tol=1e-9,abs_tol=1e-9)
assert readable._canvas.pixels_per_second>=240,'Readable zoom did not restore the horizontal default'
assert readable.selection()==selection_snapshot
selected_center=sum(selection_snapshot)/2
content_center=(readable._canvas.PIANO_WIDTH+readable._view.viewport().width())/2
assert abs(readable.view_x_for_time(selected_center)-content_center)<=1.5,'Readable zoom did not centre the existing selection'
assert abs(pitch_center(readable)-66)<=.25,'Readable zoom did not centre the visible phrase pitch'

readable.clear_selection();readable.set_zoom(.3);application.processEvents()
readable.set_zoom(1.0);bar=readable._view.horizontalScrollBar();bar.setValue((bar.minimum()+bar.maximum())//2);application.processEvents()
center_x=(readable._canvas.PIANO_WIDTH+readable._view.viewport().width())/2;before_center=readable.time_at_view_x(center_x)
readable.readable_zoom();application.processEvents()
assert math.isclose(readable.zoom_factor(),3.25,rel_tol=1e-9,abs_tol=1e-9) and readable.selection() is None
assert abs(readable.time_at_view_x(center_x)-before_center)<=.02,'Readable zoom moved the centre time without a selection'

readable.fit_project();application.processEvents();fit_zoom=readable.zoom_factor()
assert not math.isclose(fit_zoom,3.25,rel_tol=1e-9,abs_tol=1e-9),'Fit did not change the zoom in this test viewport'
readable.readable_zoom();application.processEvents()
assert math.isclose(readable.zoom_factor(),3.25,rel_tol=1e-9,abs_tol=1e-9)
readable.fit_project();application.processEvents()
assert math.isclose(readable.zoom_factor(),fit_zoom,rel_tol=1e-9,abs_tol=1e-9),'Fit/readable zoom did not round trip'
assert readable_project==project_snapshot and readable_regions==regions_snapshot
readable.close();application.processEvents()
print('PASS: readable pitch defaults, pitch-height bounds/centre preservation, invalid-input immutability and readable/fit round trip')
