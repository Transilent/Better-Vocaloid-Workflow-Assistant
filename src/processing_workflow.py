"""Processing presets and step choices; legacy graph storage remains compatible."""
import copy
import os
from PyQt5.QtWidgets import QWidget, QInputDialog, QMessageBox
import workflow


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
        self.apply_options(state['options'])
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
        owner=self.owner
        projects=[fmt for fmt,check in owner.project_checks.items() if check.isChecked()]
        audio=[fmt for fmt,check in owner.audio_checks.items() if check.isChecked()] or ['wav']
        enabled=owner.exports_enabled.isChecked()
        return workflow.validate_options({'language':owner.language.currentData(),'recognize_lyrics':owner.recognize.isChecked(),
            'zh_lyric_mode':owner.zh_lyric_mode.currentData(),'voice_mode':owner.separator_model.currentData() if owner.voice_mode.currentData()=='dual' else owner.voice_mode.currentData(),
            'midi_steps':owner.midi_steps.currentData(),'separation_device':owner.separation_device.currentData(),
            'midi_device':owner.midi_device.currentData(),'roformer_device':owner.roformer_device.currentData(),
            'dml_adapter':owner.dml_adapter.currentData(),'cpu_threads':owner.cpu_threads.currentData(),
            'midi_backend':'v2','alignment_engine':owner.alignment_engine.currentData(),
            'reference_mode':owner.reference_mode.currentData(),'subtitles_mode':owner.subtitles.match_mode.currentData(),
            'project_formats':projects if enabled and owner.midi_enabled.isChecked() else [],
            'audio_formats':audio if owner.audio_enabled.isChecked() else ['wav'],'export_pitch':owner.export_pitch.isChecked(),
            'include_instrumental':owner.include_instrumental.isChecked()})

    def apply_options(self,values):
        values=workflow.validate_options(values)
        owner=self.owner
        for key in ('language','zh_lyric_mode','roformer_device','midi_steps','separation_device','midi_device','alignment_engine','reference_mode','dml_adapter','cpu_threads'):
            field=getattr(owner,key)
            if key in ('cpu_threads','dml_adapter') and field.findData(values[key])<0:
                field.addItem(str(values[key]) if key=='cpu_threads' else f'已保存显卡 {values[key]}（当前不可用）',values[key])
            field.setCurrentIndex(max(0,field.findData(values[key])))
        owner.subtitles.match_mode.setCurrentIndex(owner.subtitles.match_mode.findData(values['subtitles_mode']))
        mode=values['voice_mode']
        owner.voice_mode.setCurrentIndex(owner.voice_mode.findData('dual' if mode=='roformer' else mode))
        owner.separator_model.setCurrentIndex(owner.separator_model.findData('roformer' if mode=='roformer' else 'dual'))
        owner.recognize.setChecked(values['recognize_lyrics'])
        owner.export_pitch.setChecked(values['export_pitch'])
        owner.include_instrumental.setChecked(values['include_instrumental'])
        for fmt,check in owner.project_checks.items(): check.setChecked(fmt in values['project_formats'])
        for fmt,check in owner.audio_checks.items(): check.setChecked(fmt in values['audio_formats'])
        owner.exports_enabled.setChecked(bool(values['project_formats']))
        owner.audio_enabled.setChecked(True)

    def sync_from_owner(self):
        pass

    def matching_preset(self):
        for name,preset in {**workflow.builtins(),**self.presets}.items():
            # Stored positions are irrelevant to the ordered step interface.
            if workflow.validate_graph(preset['graph'])!=workflow.validate_graph(self.graph): continue
            expected=workflow.validate_options(preset['options'])
            expected['midi_backend']='v2'
            if not self.owner.midi_enabled.isChecked(): expected['project_formats']=[]
            if expected==self.options(): return name
        return '自定义'

    def sync_preset_picker(self):
        self.reload_presets(self.preset.currentText())

    def reload_presets(self,selected='自定义'):
        picker=self.preset
        picker.blockSignals(True)
        picker.clear()
        for name in ['自定义',*workflow.builtins(),*self.presets]: picker.addItem(name,name)
        picker.setCurrentIndex(max(0,picker.findText(selected)))
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
        if os.environ.get('BVWA_TESTING')!='1' or getattr(self,'_persist_test',False):
            workflow.save_state(self.graph,self.options(),self.presets,self.preset.currentText())
        self.owner.refresh_workflow_summary()

    def save_preset(self):
        name,ok=QInputDialog.getText(self.owner,'保存预设','预设名称（只保存处理参数，不包含歌曲和歌词）')
        name=name.strip()
        if not ok: return
        if not name or len(name)>80 or name.startswith('内置') or name=='自定义':
            QMessageBox.warning(self.owner,'名称无效','请填写 1–80 字的名称，不以“内置”开头。')
            return
        if name in self.presets and QMessageBox.question(self.owner,'覆盖预设','覆盖同名预设？')!=QMessageBox.Yes: return
        self.presets[name]={'graph':self.graph,'options':self.options()}
        self.reload_presets(name)
        self.persist(dirty=False)

    def delete_preset(self):
        name=self.preset.currentText()
        if name in self.presets and QMessageBox.question(self.owner,'删除预设','删除预设“'+name+'”？')==QMessageBox.Yes:
            del self.presets[name]
            self.reload_presets()
            self.persist(dirty=False)
