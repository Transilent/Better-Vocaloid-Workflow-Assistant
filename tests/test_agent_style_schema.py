"""Style catalogue drives strict typed requests, including future catalogue entries."""
import ast
import os
from pathlib import Path
import subprocess
import sys
from typing import Literal, get_args

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))
import style_catalog

source = APP / 'src/agent_mcp_models.py'
tree = ast.parse(source.read_text(encoding='utf-8'))
aliases = {'StylePresetId', 'StyleTechniqueId'}
bindings = [node for node in tree.body if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id in aliases for target in node.targets)]
assert len(bindings) == 2
namespace = {'Literal': Literal, 'VISIBLE_STYLES': style_catalog.VISIBLE_STYLES,
             'TECHNIQUE_IDS': style_catalog.TECHNIQUE_IDS}
exec(compile(ast.Module(body=bindings, type_ignores=[]), str(source), 'exec'), namespace)
assert get_args(namespace['StylePresetId']) == style_catalog.VISIBLE_STYLES
assert get_args(namespace['StyleTechniqueId']) == style_catalog.TECHNIQUE_IDS
region = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'StyleRegion')
annotations = {node.target.id: node.annotation for node in region.body if isinstance(node, ast.AnnAssign)}
assert isinstance(annotations['preset'], ast.Name) and annotations['preset'].id == 'StylePresetId'
assert isinstance(annotations['style_technique'], ast.Name) and annotations['style_technique'].id == 'StyleTechniqueId'

SDK_SCRIPT = r'''
from pathlib import Path
import json,sys,types
from unittest.mock import patch
sys.path.insert(0,str(Path(sys.argv[1])/'src'))
from pydantic import ValidationError
import agent_mcp_models as models
import style_catalog as catalogue

def values(preset='none', technique='none'):
    return {'preset':preset,'strength':.6,'rationale':'typed style fixture',
            'start':0.,'end':1.,'style_technique':technique}

count=0
for style in catalogue.VISIBLE_STYLES:
    for technique in catalogue.TECHNIQUE_IDS:
        result=models.StyleRegion.model_validate(values(style,technique))
        assert result.preset==style and result.style_technique==technique
        count+=1
for invalid in (*catalogue.LEGACY_STYLES,'chinese_opera','unknown',True,4):
    try:models.StyleRegion.model_validate(values(invalid))
    except ValidationError:pass
    else:raise AssertionError(('public canonical style accepted invalid value',invalid))
for invalid in ('gentle','unknown',True):
    try:models.StyleRegion.model_validate(values(technique=invalid))
    except ValidationError:pass
    else:raise AssertionError(('technique accepted invalid value',invalid))
schema=models.StyleRegion.model_json_schema()
assert schema['properties']['preset']['enum']==list(catalogue.VISIBLE_STYLES)
assert schema['properties']['style_technique']['enum']==list(catalogue.TECHNIQUE_IDS)
alias=values('sad');alias['technique']=alias.pop('style_technique')
assert 'style_technique' in models.StyleRegion.model_validate(alias).model_dump()

# Extend only a private catalogue module. Fresh model generation must discover
# the new ID without another hard-coded enum edit, while loaded models stay put.
extra='private_future_style_fixture'
fake=types.ModuleType('style_catalog')
fake.VISIBLE_STYLES=(*catalogue.VISIBLE_STYLES,extra)
fake.TECHNIQUE_IDS=catalogue.TECHNIQUE_IDS
fake.DEFAULT_TECHNIQUE_ID=catalogue.DEFAULT_TECHNIQUE_ID
module=types.ModuleType('private_extended_style_models')
module.__file__=models.__file__
sys.modules[module.__name__]=module
try:
    with patch.dict(sys.modules,{'style_catalog':fake}):
        exec(compile(Path(models.__file__).read_text(encoding='utf-8'),models.__file__,'exec'),module.__dict__)
    assert module.StyleRegion.model_validate(values(extra)).preset==extra
    assert module.StyleRegion.model_json_schema()['properties']['preset']['enum']==list(fake.VISIBLE_STYLES)
finally:sys.modules.pop(module.__name__,None)
assert models.StyleRegion.model_json_schema()==schema
print(json.dumps({'typed_cases':count,'strict_invalid_values_rejected':True,
                 'json_schema_matches_catalogue':True,'registry_extension_propagates':True}))
'''

sdk = os.environ.get('VWA_MCP_SDK_PYTHON')
if sdk:
    result = subprocess.run([sdk, '-B', '-c', SDK_SCRIPT, str(APP)], cwd=APP,
                            capture_output=True, timeout=45)
    if result.returncode:
        print((result.stdout + result.stderr).decode('utf-8', errors='replace'))
        raise SystemExit(result.returncode)
    print(result.stdout.decode('utf-8').strip())
else:
    print('SKIP: strict Pydantic execution needs VWA_MCP_SDK_PYTHON; pure catalogue/type binding passed')
print('PASS: style registry controls canonical typed IDs, strict validation and JSON schema')
