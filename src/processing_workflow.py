"""Processing presets and step choices; legacy graph storage remains compatible."""

from common import environment_value
from i18n import tr as _tr
import copy
import os
from PyQt5.QtWidgets import QWidget, QInputDialog, QMessageBox
import workflow


TONAL_KEYS=('reference_dynamics','dynamics_strength','reference_tension','reference_breathiness',
            'reference_voicing','expression_density','performance_style','style_strength','style_scope','style_pitch')


def _owner_tuning_preferences(owner):
    from workflow import DEFAULT_OPTIONS
    result={key:DEFAULT_OPTIONS[key] for key in TONAL_KEYS}
    for key in ('reference_dynamics','reference_tension','reference_breathiness','reference_voicing','style_pitch'):
        field=getattr(owner,key,None)
        if field is not None:result[key]=bool(field.isChecked())
    for key in ('dynamics_strength','expression_density','performance_style','style_strength','style_scope'):
        field=getattr(owner,key,None)
        if field is not None and field.currentData() is not None:result[key]=field.currentData()
    density=result['expression_density']
    result['expression_density']=density if density in ('dense','original') else 'original'
    style=result['performance_style']
    result['performance_style']=style if style in ('none','soft_ballad','intimate_narration','power_pop') else 'none'
    result['style_scope']=result['style_scope'] if result['style_scope'] in ('lead','all_vocals') else 'lead'
    for key in ('dynamics_strength','style_strength'):
        try:value=float(result[key])
        except (TypeError,ValueError):value=.6
        result[key]=min(1.,max(0.,value))
    return result


def _prep_preferences(values):
    # Presets compare preparation choices only; the independent tuning page owns
    # the current expression and style selections.
    from workflow import DEFAULT_OPTIONS
    result={**copy.deepcopy(DEFAULT_OPTIONS),**values}
    return {key:result[key] for key in DEFAULT_OPTIONS if key not in TONAL_KEYS}


class ProcessingWorkflow(QWidget):
    def __init__(self,owner):
        super().__init__(owner)
        self.hide()
        self.owner,self.loading=owner,True
        state=workflow.read_state()
        self.presets=state['presets']
        self.preset=owner.workflow_preset
        self.fields={'midi_steps':owner.midi_steps,'separation_device':owner.separation_device,'midi_device':owner.midi_device}
        self.reload_presets(state['preset'])
        restored_options = dict(state['options'])
        # Presets contain parameters, not a song's lyrics or account data.
        # An empty, restored online selection must not block a fresh task.
        if restored_options['reference_mode'] == 'netease':
            restored_options['reference_mode'] = 'local'
            owner.local_reference_status.setText(_tr('请重新搜索网易云歌词，或在本地填写歌词。'))
        self.apply_options(restored_options)
        self.graph=state['graph']
        self.loading=False

    @property
    def graph(self):
        if not self.owner.midi_enabled.isChecked():
            return {'nodes':{'source':[60,150],'separation':[295,150]},'edges':[['source','separation']]}
        return workflow.default_graph(self.owner.subtitles_enabled.isChecked())

    @graph.setter
    def graph(self,graph):
        target=workflow.validate_graph(graph)
        self.owner.midi_enabled.setChecked(target!='separation')
        self.owner.subtitles_enabled.setChecked(target=='subtitles')

    def options(self):
        # workflow.execution_options is the sole owner of the GUI preparation
        # policy: prepare_only, no tonal stages and no calibration profile.
        return workflow.execution_options(self.preferences(),midi_enabled=self.owner.midi_enabled.isChecked())

    def preferences(self):
        owner=self.owner
        projects=[fmt for fmt,check in owner.project_checks.items() if check.isChecked()]
        audio=[fmt for fmt,check in owner.audio_checks.items() if check.isChecked()]
        values={'language':owner.language.currentData(),'recognize_lyrics':owner.recognize.isChecked(),
            'zh_lyric_mode':owner.zh_lyric_mode.currentData(),
            'voice_mode':owner.separator_model.currentData() if owner.voice_mode.currentData()=='dual' else owner.voice_mode.currentData(),
            'midi_steps':owner.midi_steps.currentData(),
            'bpm':owner.bpm.value() if owner.manual_bpm.isChecked() else None,
            'separation_device':owner.separation_device.currentData(),
            'midi_device':owner.midi_device.currentData(),'roformer_device':owner.roformer_device.currentData(),
            'separation_flow':owner.separation_flow.currentData() if owner.separator_model.currentData()=='roformer' and owner.voice_mode.currentData()=='dual' else 'cascade',
            'dml_adapter':owner.dml_adapter.currentData(),'cpu_threads':owner.cpu_threads.currentData(),
            'midi_backend':'v2','alignment_engine':owner.alignment_engine.currentData(),
            'reference_mode':owner.reference_mode.currentData(),'subtitles_mode':owner.subtitles.match_mode.currentData(),
            'project_formats':projects,'project_export_enabled':owner.exports_enabled.isChecked(),
            'audio_formats':audio,'audio_export_enabled':owner.audio_enabled.isChecked(),'export_pitch':owner.export_pitch.isChecked(),
            'include_instrumental':owner.include_instrumental.isChecked()}
        values.update(_owner_tuning_preferences(owner))
        return workflow.validate_options(values)

    def apply_options(self,values):
        current={**_owner_tuning_preferences(self.owner),**values}
        values=workflow.validate_options(current)
        owner=self.owner
        for key in ('language','zh_lyric_mode','roformer_device','midi_steps','separation_device','midi_device','alignment_engine','reference_mode','dml_adapter','cpu_threads','separation_flow'):
            field=getattr(owner,key)
            if key in ('cpu_threads','dml_adapter') and field.findData(values[key])<0:
                field.addItem(str(values[key]) if key == 'cpu_threads' else _tr('已保存显卡 {0}（当前不可用）', f'{values[key]}'),values[key])
            field.setCurrentIndex(max(0,field.findData(values[key])))
        owner.subtitles.match_mode.setCurrentIndex(owner.subtitles.match_mode.findData(values['subtitles_mode']))
        mode=values['voice_mode']
        owner.voice_mode.setCurrentIndex(owner.voice_mode.findData('dual' if mode=='roformer' else mode))
        owner.separator_model.setCurrentIndex(owner.separator_model.findData('roformer' if mode=='roformer' else 'dual'))
        owner.recognize.setChecked(values['recognize_lyrics'])
        owner.bpm.setValue(values['bpm'] if values['bpm'] is not None else 120.)
        owner.manual_bpm.setChecked(values['bpm'] is not None)
        owner.export_pitch.setChecked(values['export_pitch'])
        owner.include_instrumental.setChecked(values['include_instrumental'])
        for fmt,check in owner.project_checks.items():check.setChecked(fmt in values['project_formats'])
        for fmt,check in owner.audio_checks.items():check.setChecked(fmt in values['audio_formats'])
        owner.exports_enabled.setChecked(values['project_export_enabled'])
        owner.audio_enabled.setChecked(values['audio_export_enabled'])

    def sync_from_owner(self):
        pass

    def matching_preset(self):
        current=_prep_preferences(self.preferences())
        for name,preset in {**workflow.builtins(),**self.presets}.items():
            # Stored graph positions and independent tuning preferences are not
            # part of preparation preset identity.
            if workflow.validate_graph(preset['graph'])!=workflow.validate_graph(self.graph):continue
            expected=_prep_preferences(workflow.validate_options(preset['options']))
            expected['midi_backend']='v2'
            if expected==current:return name
        return '自定义'

    def sync_preset_picker(self):
        self.reload_presets(self.preset.currentData())

    def reload_presets(self,selected='自定义'):
        picker=self.preset
        picker.blockSignals(True)
        picker.clear()
        for name in ['自定义',*workflow.builtins(),*self.presets]:
            picker.addItem(_tr(name) if name not in self.presets else name, name)
        picker.setCurrentIndex(max(0,picker.findData(selected)))
        picker.blockSignals(False)

    def choose_preset(self,name):
        if self.loading or self.owner.processing_active: return
        if name!='自定义':
            preset={**workflow.builtins(),**self.presets}.get(name)
            if not preset: return
            self.loading=True
            self.apply_options(preset['options'])
            self.graph=copy.deepcopy(preset['graph'])
            self.loading=False
        self.preset.blockSignals(True)
        self.preset.setCurrentIndex(max(0,self.preset.findData(name)))
        self.preset.blockSignals(False)
        self.persist(dirty=False)

    def persist(self,dirty=True):
        if self.loading or self.owner.processing_active: return
        if dirty:
            self.preset.blockSignals(True)
            self.preset.setCurrentIndex(0)
            self.preset.blockSignals(False)
        if environment_value('VWA_TESTING')!='1' or getattr(self,'_persist_test',False):
            workflow.save_state(self.graph,self.preferences(),self.presets,self.preset.currentData())
        self.owner.refresh_workflow_summary()

    def save_preset(self):
        name,ok=QInputDialog.getText(self.owner,_tr('保存预设'),_tr('预设名称（只保存处理参数，不包含歌曲和歌词）'))
        name=name.strip()
        if not ok: return
        if not name or len(name)>80 or name.startswith('内置') or name=='自定义':
            QMessageBox.warning(self.owner,_tr('名称无效'),_tr('请填写 1–80 字的名称，不以“内置”开头。'))
            return
        if name in self.presets and QMessageBox.question(self.owner,_tr('覆盖预设'),_tr('覆盖同名预设？'))!=QMessageBox.Yes: return
        self.presets[name]={'graph':self.graph,'options':self.preferences()}
        self.reload_presets(name)
        self.persist(dirty=False)

    def delete_preset(self):
        name=self.preset.currentData()
        if name in self.presets and QMessageBox.question(self.owner,_tr('删除预设'),_tr(_tr('删除预设“') + name + '”？'))==QMessageBox.Yes:
            del self.presets[name]
            self.reload_presets()
            self.persist(dirty=False)
