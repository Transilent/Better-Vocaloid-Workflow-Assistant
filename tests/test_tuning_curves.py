"""Fractional tuning curves, preservation, density and blick-collision guards."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP/'src'))
from calibration_refinement import finalize_expression
from style_presets import apply_style,apply_style_svp
from tuning_engine import process_project
from vocal_expression import SV_QUARTER as Q,TempoMap,automation

TEST_TEMP=Path(os.environ.get('BVWA_CURVES_TMP',str(APP/'work/test-temp'))).resolve()


def project(*, integer_tempo=False):
    tempo=[{'position':0,'bpm':120}]
    if not integer_tempo:
        tempo.append({'position':Q,'bpm':100})
    return {
        'version':'1.0.0','uuid':'curve-fixture','library':[{'name':'song'}],
        'time':{'tempo':tempo,'meter':[{'index':0,'numerator':4,'denominator':4}]},
        'tracks':[{
            'name':'Lead Vocal',
            'mainRef':{'blickOffset':Q//3,'isInstrumental':False,'database':{'name':'test bank'}},
            'mainGroup':{
                'notes':[{'onset':0,'duration':2*Q,'pitch':60,'lyrics':'a'}],
                'parameters':{
                    'loudness':{'mode':'linear','points':[0,0.,Q+.25,.8,2*Q+.75,-.2]},
                    'tension':{'mode':'linear','points':[.25,0.,Q+.5,.12,2*Q+.25,-.04]},
                    'breathiness':{'mode':'linear','points':[.5,.05,Q+.75,.08,2*Q+.5,.03]},
                    'voicing':{'mode':'linear','points':[.75,.9,Q+.5,.95,2*Q+.75,.91]},
                    'pitchDelta':{'mode':'cubic','points':[.25,0.,Q+.25,35.,2*Q+.25,-20.]},
                    'unknownChannel':{'mode':'cubic','points':[.125,1,Q+.125,2]},
                },
            },
        }],
        'unknownRoot':{'keep':True},
    }


def pairs(curve):
    return list(zip(curve['points'][::2],curve['points'][1::2]))


def interpolate(curve,probes):
    values=pairs(curve)
    x=np.asarray([row[0] for row in values],dtype=float)
    y=np.asarray([row[1] for row in values],dtype=float)
    return np.interp(probes,x,y)


class TuningCurveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        TEST_TEMP.mkdir(parents=True,exist_ok=True)

    def assert_original_coordinates(self,before,after):
        old=pairs(before);new=pairs(after)
        for x,_ in old:
            matches=[(out_x,out_y) for out_x,out_y in new
                     if out_x==x and type(out_x) is type(x)]
            self.assertEqual(len(matches),1,(x,type(x),after['points'][:20]))
            self.assertIs(type(matches[0][0]),type(x))

    def test_dense_fractional_preserves_source_types_shape_and_idempotence(self):
        source=project();frozen=copy.deepcopy(source);output=copy.deepcopy(source)
        report=finalize_expression(output)
        self.assertEqual(source,frozen,'The caller project was changed by a deep-copy transaction')
        self.assertEqual(report['status'],'densified')
        self.assertEqual(report['unsupported_nonlinear_controls'],0)
        self.assertGreater(report['added_points'],0)
        for name in ('loudness','tension','breathiness','voicing'):
            before=source['tracks'][0]['mainGroup']['parameters'][name]
            after=output['tracks'][0]['mainGroup']['parameters'][name]
            self.assert_original_coordinates(before,after)
            old=pairs(before);new=pairs(after)
            for x,y in old:
                match=[pair for pair in new if pair[0]==x and type(pair[0]) is type(x)]
                self.assertEqual(match[0][1],y)
                self.assertIs(type(match[0][1]),type(y))
            probes=np.linspace(old[0][0],old[-1][0],401)
            np.testing.assert_allclose(interpolate(after,probes),interpolate(before,probes),
                                       rtol=0,atol=2e-12)
            state=report['tracks'][0]['controls'][name]
            self.assertTrue(state['fractional_input_positions'])
            self.assertTrue(state['original_coordinate_types_preserved'])
        once=copy.deepcopy(output)
        again=finalize_expression(output)
        self.assertEqual(again['added_points'],0)
        self.assertEqual(output,once,'Second density pass was not idempotent')

    def test_style_and_engine_fractional_combination_is_transactional(self):
        source=project();frozen=copy.deepcopy(source)
        output,report=process_project(source,{
            'track_indices':[0],'performance_style':'soft_ballad',
            'style_strength':.6,'style_pitch':False,'expression_density':'dense',
        })
        self.assertEqual(source,frozen,'Engine or style changed the source project')
        self.assertEqual(report['final_density_passes'],1)
        self.assertEqual(report['style']['status'],'applied')
        self.assertEqual(report['expression_density']['status'],'densified')
        self.assertEqual(report['expression_density']['unsupported_fractional_time_controls'],0)
        self.assertTrue(report['original_input_unchanged'])
        for name in ('loudness','tension','breathiness','voicing'):
            before=frozen['tracks'][0]['mainGroup']['parameters'][name]
            after=output['tracks'][0]['mainGroup']['parameters'][name]
            self.assert_original_coordinates(before,after)
            state=report['style']['tracks'][0]['controls'][name]
            self.assertTrue(state['fractional_input_positions'])
            self.assertTrue(state['original_coordinate_types_preserved'])
        self.assertEqual(output['tracks'][0]['mainGroup']['parameters']['pitchDelta'],
                         frozen['tracks'][0]['mainGroup']['parameters']['pitchDelta'])
        self.assertEqual(output['tracks'][0]['mainGroup']['parameters']['unknownChannel'],
                         frozen['tracks'][0]['mainGroup']['parameters']['unknownChannel'])

    def test_nonlinear_and_malformed_curves_fail_closed_without_writes(self):
        nonlinear=project()
        cubic={'mode':'cubic','points':[.25,0.,Q+.25,.1]}
        nonlinear['tracks'][0]['mainGroup']['parameters']={
            'loudness':{'mode':'linear','points':[0,0.,2*Q,.2]},'tension':copy.deepcopy(cubic)}
        report=finalize_expression(nonlinear)
        self.assertEqual(report['status'],'partial_unsupported')
        self.assertEqual(report['tracks'][0]['controls']['tension']['status'],'preserved_nonlinear')
        self.assertEqual(nonlinear['tracks'][0]['mainGroup']['parameters']['tension'],cubic)
        self.assertGreater(report['added_points'],0)
        self.assertGreater(len(nonlinear['tracks'][0]['mainGroup']['parameters']['loudness']['points']),2)

        cases={
            '重复':[0,0.,Q,.1,Q,.2],
            '乱序':[0,0.,Q,.1,Q/2,.2],
            '非有限':[0,0.,float('inf'),.1],
        }
        for label,points in cases.items():
            with self.subTest(label=label):
                data=project()
                data['tracks'][0]['mainGroup']['parameters']={
                    'loudness':{'mode':'linear','points':[0,0.,2*Q,.1]},
                    'tension':{'mode':'linear','points':points},
                }
                frozen=copy.deepcopy(data)
                with self.assertRaises(ValueError) as caught:
                    finalize_expression(data)
                message=str(caught.exception)
                self.assertIn('轨道=0',message)
                self.assertIn('通道=tension',message)
                self.assertIn('坐标',message)
                self.assertEqual(data,frozen)

                with self.assertRaises(ValueError):
                    apply_style(data,'soft_ballad',track_indices=[0],density_mode='original')
                self.assertEqual(data,frozen)

        with tempfile.TemporaryDirectory(prefix='curve-io-',dir=TEST_TEMP) as folder:
            root=Path(folder);source=root/'invalid.svp';destination=root/'candidate.svp'
            data=project();data['tracks'][0]['mainGroup']['parameters']['tension']={
                'mode':'linear','points':[0,0.,Q,.1,Q,.2]}
            raw=json.dumps(data,ensure_ascii=False).encode('utf-8')
            source.write_bytes(raw)
            with self.assertRaises(ValueError):
                apply_style_svp(source,destination,preset='soft_ballad',track_indices=[0])
            self.assertEqual(source.read_bytes(),raw)
            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_suffix(destination.suffix+'.style.json').exists())

    def test_integer_curve_behavior_and_types_remain_compatible(self):
        data=project(integer_tempo=True)
        data['tracks'][0]['mainGroup']['parameters']={
            'loudness':{'mode':'linear','points':[0,0,Q,.5,2*Q,0]},
            'tension':{'mode':'linear','points':[0,0.,Q,.2,2*Q,0.]},
        }
        frozen=copy.deepcopy(data)
        report=finalize_expression(data)
        self.assertEqual(report['status'],'densified')
        for name in ('loudness','tension'):
            curve=data['tracks'][0]['mainGroup']['parameters'][name]
            self.assertTrue(all(type(x) is int for x in curve['points'][::2]),curve['points'][:20])
            self.assert_original_coordinates(frozen['tracks'][0]['mainGroup']['parameters'][name],curve)
        self.assertFalse(report['tracks'][0]['controls']['tension']['fractional_input_positions'])

    def test_automation_rejects_rounding_collision_and_merges_equivalent(self):
        clock=TempoMap([{'position':0,'bpm':120}])
        samples=[[0.,0.],[.02,.1],[.04,.2]]
        curve=automation(samples,clock,0)
        self.assertEqual(curve,{'mode':'linear','points':[0,0.,28224000,.1,56448000,.2]})

        merged=automation([[0.,.25],[1e-12,.25]],clock,0)
        self.assertEqual(len(merged['points']),2)
        self.assertEqual(merged['rounding_collision_policy'],'merge_equivalent_reject_distinct')
        self.assertEqual(len(merged['rounding_collision_merges']),1)
        self.assertEqual(merged['rounding_collision_merges'][0]['blick'],0)

        with self.assertRaisesRegex(ValueError,'取整碰撞.*秒.*blick.*拒绝静默覆盖'):
            automation([[0.,0.],[1e-12,1.]],clock,0)
        with self.assertRaisesRegex(ValueError,'时间重复'):
            automation([[0.,0.],[0.,1.]],clock,0)
        with self.assertRaisesRegex(ValueError,'时间乱序'):
            automation([[.1,0.],[.05,1.]],clock,0)
        with self.assertRaisesRegex(ValueError,'有限数值'):
            automation([[0.,float('nan')]],clock,0)


if __name__=='__main__':
    unittest.main()