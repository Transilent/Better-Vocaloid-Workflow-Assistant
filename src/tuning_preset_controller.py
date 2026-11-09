"""Frozen preset provenance over the existing tuning form, without Qt or I/O."""
import copy
import json

from tuning_preset_documents import make_document, plan_application, validate_plan


class TuningPresetController:
    def __init__(self):
        self._plan_json = None
        self.detached = False
        self.error = None

    @property
    def plan(self):
        return json.loads(self._plan_json) if self._plan_json is not None else None

    @property
    def active(self):
        return self._plan_json is not None

    def clear(self, *, detached=False):
        self._plan_json = None
        self.detached = detached
        self.error = None

    def invalidate(self,message):
        self.clear()
        self.error = str(message)

    def activate(self, plan):
        rebuilt = plan_application(plan['document'], plan['project'], plan['bindings'],
                                   raw_document=plan['document_text'])
        for field in ('document', 'document_text', 'preset', 'project', 'input_sha256',
                      'bindings', 'controls', 'options', 'missing'):
            if rebuilt[field] != plan[field]:
                raise ValueError('调音预设方案已变化，请重新预览。')
        self._plan_json = json.dumps(rebuilt, ensure_ascii=False, allow_nan=False)
        self.detached = False
        self.error = None

    def reconcile(self, project, controls, bindings):
        current = self.plan
        if current is None:
            return None
        if project is None or project.get('sha256') != current['project']['sha256']:
            self.clear()
            return None
        try:
            payload = make_document(controls, 'Current form')['payload']
        except (ValueError, TypeError, KeyError):
            self.clear(detached=True)
            return None
        if payload != current['document']['payload']:
            self.clear(detached=True)
            return None
        plan = plan_application(current['document'], project, bindings,
                                raw_document=current['document_text'])
        self._plan_json = json.dumps(plan, ensure_ascii=False, allow_nan=False)
        return copy.deepcopy(plan)

    def for_execution(self, project, controls, bindings):
        if self.error:
            raise ValueError(self.error)
        plan = self.reconcile(project, controls, bindings)
        if plan is None:
            return None
        return validate_plan(plan, project=project)
