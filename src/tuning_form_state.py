"""One adapter between tuning widgets, local input bindings and engine options.

The widgets remain the editable state. This adapter owns no second model and
does no filesystem or acoustic work. Session restore and preset application
use the same writer that execution's parameter capture reads.
"""
from __future__ import annotations

import copy


class TuningFormState:
    def __init__(self, page):
        self.page = page

    def controls(self):
        p = self.page
        return {**p.preferences(), 'expression_mode': p.mode.currentData(),
                'style_regions': copy.deepcopy(p.style_regions)
                if p.style_mode.currentData() == 'regions' else []}

    def bindings(self, *, confirm_candidate_baseline=False):
        p = self.page
        selected = [row for row in p.rows if row['check'].isChecked()]
        return {
            'vocal_tracks': [row['track_index'] for row in selected],
            'reference_audio': [{'track_index': row['track_index'],
                                 'voice': row['voice'].currentData(),
                                 'path': row['path'].text().strip()} for row in selected],
            'calibration_profile': p.calibration_file.text().strip(),
            'retained_project': [{'track_index': row['track_index'],
                                  'project': p.retained_project.text().strip(),
                                  'source_track_index': row['source_track'].value() - 1}
                                 for row in selected],
            'confirm_candidate_baseline': bool(confirm_candidate_baseline),
        }

    def snapshot(self):
        p=self.page
        return {'controls':self.controls(), 'calibration_profile':p.calibration_file.text(),
                'retained_project':p.retained_project.text(),
                'rows':[{'track_index':r['track_index'],'selected':r['check'].isChecked(),
                         'voice':r['voice'].currentData(),'path':r['path'].text(),
                         'source_track_index':r['source_track'].value()-1} for r in p.rows]}

    def restore(self,snapshot):
        rows=snapshot['rows']
        self.apply(snapshot['controls'],{
            'vocal_tracks':[r['track_index'] for r in rows if r['selected']],
            'reference_audio':[{k:r[k] for k in ('track_index','voice','path')} for r in rows],
            'calibration_profile':snapshot['calibration_profile'],
            'retained_project':[{'track_index':r['track_index'],'project':snapshot['retained_project'],
                                 'source_track_index':r['source_track_index']} for r in rows]})
        p=self.page;previous=p.loading;p.loading=True
        try:
            p.calibration_file.setText(snapshot['calibration_profile'])
            p.retained_project.setText(snapshot['retained_project'])
        finally:p.loading=previous

    def options(self):
        from tuning_engine import settings
        controls = self.controls()
        mode = controls['expression_mode']
        local = self.bindings()
        values = {**controls, 'track_indices': local['vocal_tracks']}
        for name in ('dynamics', 'tension', 'breathiness', 'voicing'):
            values['reference_' + name] = mode == 'reference' and values['reference_' + name]
        if mode in ('reference', 'calibrated'):
            values['reference_sources'] = local['reference_audio']
        if mode == 'calibrated':
            values['reference_calibration'] = local['calibration_profile']
        if mode == 'retained':
            values['retained_sources'] = local['retained_project']
        return settings(values)

    @staticmethod
    def from_options(options):
        """Separate existing session options into controls and local bindings."""
        values = copy.deepcopy(options)
        selected = values.get('track_indices') or []
        retained = values.get('retained_sources') or []
        if not retained and values.get('retained_project'):
            retained = [{'track_index': index, 'project': values['retained_project'],
                         'source_track_index': values.get('retained_track_index', 0)}
                        for index in selected]
        return values, {
            'vocal_tracks': selected,
            'reference_audio': values.get('reference_sources') or [],
            'calibration_profile': values.get('reference_calibration') or '',
            'retained_project': retained,
        }

    def apply(self, controls, bindings):
        """Apply a proposal in one guarded widget update, without file I/O.

        This page currently offers one retained-project field. Refuse an
        unrepresentable multi-project proposal before changing any widgets.
        """
        p = self.page
        mode = controls.get('expression_mode', 'preserve')
        if p.mode.findData(mode) < 0:
            raise ValueError('请选择有效的参数来源。')
        selected = list(bindings.get('vocal_tracks') or [])
        known = {row['track_index'] for row in p.rows}
        if len(selected) != len(set(selected)) or not set(selected).issubset(known):
            raise ValueError('预设绑定包含当前工程以外或重复的演唱轨。')
        references = {row['track_index']: row for row in bindings.get('reference_audio') or []}
        retained = {row['track_index']: row for row in bindings.get('retained_project') or []}
        paths = {row['project'] for row in retained.values() if row.get('project')}
        if mode == 'retained' and len(paths) > 1:
            raise ValueError('当前表单每次沿用一个原参数工程，请为所选轨选择同一工程。')
        previous = (p.loading, p._restoring_preferences if hasattr(p, '_restoring_preferences') else False,
                    p._selecting_scope)
        p.loading = p._restoring_preferences = p._selecting_scope = True
        try:
            p.apply_preferences(controls)
            p.mode.setCurrentIndex(p.mode.findData(mode))
            p.calibration_file.setText(str(bindings.get('calibration_profile') or '') if mode == 'calibrated' else '')
            p.retained_project.setText(next(iter(paths), '') if mode == 'retained' else '')
            for row in p.rows:
                index = row['track_index']
                row['check'].setChecked(index in selected)
                reference = references.get(index)
                if reference is not None:
                    voice = row['voice'].findData(reference.get('voice'))
                    if voice < 0 and reference.get('voice') not in (None, ''):
                        raise ValueError('参考音频绑定的声部角色无效。')
                    row['voice'].setCurrentIndex(voice)
                    row['path'].setText(str(reference.get('path') or ''))
                elif index in selected and mode in ('reference', 'calibrated'):
                    row['path'].clear()
                if index in retained:
                    source_index=retained[index].get('source_track_index')
                    row['source_track'].setValue(0 if source_index is None else int(source_index)+1)
        finally:
            p.loading, p._restoring_preferences, p._selecting_scope = previous
        p._track_selection_changed()

    def apply_options(self, options, *, restore_regions=False):
        controls, bindings = self.from_options(options)
        self.apply(controls, bindings)
        if restore_regions:
            self.page.restore_style_regions(copy.deepcopy(options.get('style_regions') or []))
