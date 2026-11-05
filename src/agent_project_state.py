"""Current project identity, separate from a workflow's retained audit history."""
from __future__ import annotations


def current_style(value):
    """Return the current style binding, or None for an unstyled generation.

    Older records have no binding. Only their latest project transition is
    relevant; an earlier successful style action must not style a later export.
    Consumers still verify the operation, artifact and receipt hashes.
    """
    if 'style_result' in value:
        binding = value['style_result']
        if not isinstance(binding, dict):
            return None
        if binding.get('generation') != value.get('project_generation', 0):
            return None
        return binding
    for row in reversed(value.get('history', [])):
        if not isinstance(row, dict):
            continue
        action = row.get('action')
        if action == 'styled_project_ready':
            return {'operation_id': value.get('operation_id'),
                    'artifact_id': value.get('project_artifact_id')}
        if action in ('score_rebound', 'project_exported', 'project_ready'):
            return None
    return None
