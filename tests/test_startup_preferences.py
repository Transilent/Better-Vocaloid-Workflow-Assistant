"""First-run and per-version reminder choices survive restart and cache cleanup."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))
import startup_preferences as state


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='startup-state-',dir=APP/'work')
        self.root=Path(self.temp.name)
        (self.root/'config.json').write_text(json.dumps({'python':'keep.exe','theme':'dark','other':{'x':1}}),encoding='utf-8')
    def tearDown(self):self.temp.cleanup()
    def test_first_open_skip_and_completed_persist(self):
        self.assertTrue(state.tour_due(state.load_preferences(self.root)))
        state.finish_tour(self.root,'closed')
        self.assertTrue(state.tour_due(state.load_preferences(self.root)))
        state.finish_tour(self.root,'skipped')
        self.assertFalse(state.tour_due(state.load_preferences(self.root)))
        state.finish_tour(self.root,'completed')
        config=json.loads((self.root/'config.json').read_text())
        self.assertEqual(config['other'],{'x':1})
        self.assertEqual(config['python'],'keep.exe')
        self.assertEqual(config['startup_experience']['tour_outcome'],'completed')
        self.assertFalse(state.tour_due({'tour_version':state.TOUR_VERSION+1}))
    def test_delay_expiry_and_new_version(self):
        state.defer_reminder(self.root,'v1.2.0',86400,now=100)
        value=state.load_preferences(self.root)
        self.assertFalse(state.reminder_due('1.2.0',value,now=101))
        self.assertTrue(state.reminder_due('1.2.0',value,now=86500))
        self.assertTrue(state.reminder_due('1.3.0',value,now=101))
    def test_next_start_and_ignore_release(self):
        state.defer_reminder(self.root,'1.2.0',0,now=100)
        self.assertTrue(state.reminder_due('1.2.0',state.load_preferences(self.root),now=101))
        state.defer_reminder(self.root,'1.2.0',-1,now=100)
        self.assertFalse(state.reminder_due('1.2.0',state.load_preferences(self.root),now=10**12))
        self.assertTrue(state.reminder_due('1.2.1',state.load_preferences(self.root),now=101))
    def test_reset_preserves_other_preferences(self):
        state.finish_tour(self.root,'skipped');state.defer_reminder(self.root,'1.2.0',-1)
        state.reset_reminder(self.root)
        value=state.load_preferences(self.root)
        self.assertFalse(state.tour_due(value))
        self.assertTrue(state.reminder_due('1.2.0',value))
    def test_malformed_metadata_and_input(self):
        self.assertTrue(state.reminder_due('1.2.0',{'update_reminder':{'version':'1.2.0','until':'bad'}}))
        self.assertTrue(state.reminder_due('1.2.0',{'update_reminder':{'version':'1.2.0','until':float('inf')}}))
        raw=(self.root/'config.json').read_bytes()
        for bad in (True,-2,1.5,10**10):
            with self.assertRaises(ValueError):state.defer_reminder(self.root,'1.2.0',bad)
        with self.assertRaises(ValueError):state.defer_reminder(self.root,'../../bad',0)
        self.assertEqual((self.root/'config.json').read_bytes(),raw)
        (self.root/'config.json').write_text('[]',encoding='utf-8')
        self.assertEqual(state.load_preferences(self.root),{})
        with self.assertRaises(ValueError):state.finish_tour(self.root,'completed')
        self.assertEqual((self.root/'config.json').read_text(),'[]')


if __name__=='__main__':unittest.main()
