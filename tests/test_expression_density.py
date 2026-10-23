"""Sampling settings must preserve the continuous curve and reach real export."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from calibration_refinement import finalize_expression,finalize_svp,density_settings
from vocal_expression import TempoMap,SV_QUARTER
from workflow import validate_options,export_preferences,request_options


class DensityTests(unittest.TestCase):
    def project(self):
        return {'time':{'tempo':[{'position':0,'bpm':120},{'position':SV_QUARTER,'bpm':83}]},
            'tracks':[{'mainRef':{'blickOffset':SV_QUARTER//3,'database':{'name':'keep'}},
                'mainGroup':{'notes':[{'onset':0,'duration':2*SV_QUARTER,'pitch':60,'lyrics':'hold'}],
                    'parameters':{'tension':{'mode':'linear','points':[0,0.,SV_QUARTER,.15,2*SV_QUARTER,-.1,2000*SV_QUARTER,-.1]},
                        'voicing':{'mode':'linear','points':[0,1.,2*SV_QUARTER,1.]},
                        'pitchDelta':{'mode':'linear','points':[0,-50,SV_QUARTER,30]},
                        'unknown':{'mode':'cubic','points':[0,.3,SV_QUARTER,.6]},
                        'breathiness':{'mode':'linear','points':[]}}},'retakes':{'keep':True}}]}

    def test_linear_function_and_flat_sustain(self):
        obj=self.project();prior=copy.deepcopy(obj)
        report=finalize_expression(obj)
        self.assertEqual(report['status'],'densified')
        params=obj['tracks'][0]['mainGroup']['parameters'];old=prior['tracks'][0]['mainGroup']['parameters']
        for name in ('tension','voicing'):
            x,y=np.array(params[name]['points'][::2]),np.array(params[name]['points'][1::2])
            ox,oy=np.array(old[name]['points'][::2]),np.array(old[name]['points'][1::2])
            self.assertTrue(np.array_equal(np.interp(ox,x,y),oy))
            probes=(x[:-1]+x[1:])/2
            np.testing.assert_allclose(np.interp(probes,x,y),np.interp(probes,ox,oy),rtol=0,atol=1e-15)
            state=report['tracks'][0]['controls'][name]
            self.assertLessEqual(state['max_sampled_gap_ms'],5.00001)
            self.assertLess(state['points_after'],1000) # no ten-minute flat rest padding
        self.assertGreater(len(params['voicing']['points']),100)
        self.assertTrue(all(v==1 for v in params['voicing']['points'][1::2]))
        for name in ('tension','voicing'):params[name]=old[name]
        self.assertEqual(obj,prior)

    def test_original_serialized_bytes_and_idempotence(self):
        obj=self.project()
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'test.svp';raw=json.dumps(obj).encode()+b'\0';path.write_bytes(raw)
            self.assertFalse(finalize_svp(path,mode='original')['changed'])
            self.assertEqual(path.read_bytes(),raw)
            self.assertTrue(finalize_svp(path)['changed'])
            once=path.read_bytes()
            self.assertFalse(finalize_svp(path)['changed'])
            self.assertEqual(path.read_bytes(),once)

    def test_no_silent_nonlinear_or_empty_success(self):
        obj=self.project();obj['tracks'][0]['mainGroup']['parameters']={'tension':{'mode':'cubic','points':[0,0,SV_QUARTER,.1]}}
        before=copy.deepcopy(obj);report=finalize_expression(obj)
        self.assertEqual(report['status'],'partial_unsupported')
        self.assertEqual(report['unsupported_nonlinear_controls'],1)
        self.assertEqual(obj,before)
        obj['tracks'][0]['mainGroup']['parameters']={'tension':{'mode':'linear','points':[]}}
        self.assertEqual(finalize_expression(obj)['status'],'no_curves')

    def test_preference_validation_and_restore(self):
        self.assertEqual(density_settings({}),'dense')
        values=validate_options({'expression_density':'original'})
        prefs=export_preferences(values)
        self.assertEqual(prefs['expression_density'],'original')
        self.assertEqual(request_options({'export_preferences':prefs})['expression_density'],'original')
        for bad in ('enabled',None,False,[],{}):
            with self.assertRaises(ValueError):validate_options({'expression_density':bad})

    def test_only_svp_signature_depends_on_setting(self):
        from project_exports import project_signatures
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for voice in ('lead','backing'):
                p=root/'midi'/voice/(voice+'.ustx');p.parent.mkdir(parents=True);p.write_text('test')
            options={'language':'zh','project_formats':['svp','ustx','vsqx']}
            dense=project_signatures(root,options)
            original=project_signatures(root,{**options,'expression_density':'original'})
            self.assertNotEqual(dense['svp'],original['svp'])
            self.assertEqual(dense['ustx'],original['ustx'])
            self.assertEqual(dense['vsqx'],original['vsqx'])

    def test_single_knot_is_sampled_on_notes_without_motion(self):
        obj=self.project()
        obj['tracks'][0]['mainGroup']['parameters']={'tension':{'mode':'linear','points':[0,.25]}}
        report=finalize_expression(obj)
        self.assertEqual(report['status'],'densified')
        points=obj['tracks'][0]['mainGroup']['parameters']['tension']['points']
        self.assertGreater(len(points),100)
        self.assertTrue(all(v==.25 for v in points[1::2]))
        self.assertEqual(dict(zip(points[::2],points[1::2]))[0],.25)
        once=copy.deepcopy(obj)
        self.assertEqual(finalize_expression(obj)['added_points'],0)
        self.assertEqual(obj,once)
        obj=self.project();obj['tracks'][0]['mainGroup']['notes']=[]
        obj['tracks'][0]['mainGroup']['parameters']={'tension':{'mode':'linear','points':[0,.25]}}
        self.assertEqual(finalize_expression(obj)['status'],'no_sampling_span')

    def test_finite_source_values_are_not_solver_range_validated(self):
        for curve_mode in ('linear','cubic'):
            obj=self.project()
            obj['tracks'][0]['mainGroup']['parameters']={'tension':{'mode':curve_mode,'points':[0,-2.,SV_QUARTER,2.]}}
            before=copy.deepcopy(obj)
            self.assertEqual(finalize_expression(obj,mode='original')['status'],'preserved_original')
            self.assertEqual(obj,before)
            report=finalize_expression(obj)
            state=report['tracks'][0]['controls']['tension']
            self.assertTrue(state['outside_nominal_solver_range'])
            if curve_mode=='cubic':
                self.assertEqual(report['status'],'partial_unsupported')
                self.assertEqual(obj,before)
            else:
                values=obj['tracks'][0]['mainGroup']['parameters']['tension']['points'][1::2]
                self.assertEqual((min(values),max(values)),(-2.,2.))

    def test_original_mode_does_not_parse_unused_note_durations(self):
        obj=self.project();obj['tracks'][0]['mainGroup']['notes'][0]['duration']=0
        before=copy.deepcopy(obj)
        self.assertEqual(finalize_expression(obj,mode='original')['status'],'preserved_original')
        self.assertEqual(obj,before)


if __name__=='__main__':unittest.main()
