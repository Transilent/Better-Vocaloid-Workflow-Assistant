import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from runner_contract import classify_output, run_bounded_process


class BoundedProcessTests(unittest.TestCase):
    def test_timeout_reclaims_owned_descendant_tree(self):
        root = APP / 'work/temp'
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='runner-tree-', dir=root) as temporary:
            folder = Path(temporary)
            started = folder / 'child-started'
            marker = folder / 'descendant-survived'
            grandchild = folder / 'grandchild.py'
            parent = folder / 'parent.py'
            grandchild.write_text(
                "import pathlib, sys, time\n"
                "time.sleep(1.6)\n"
                "pathlib.Path(sys.argv[1]).write_text('survived', encoding='utf-8')\n",
                encoding='utf-8',
            )
            parent.write_text(
                "import pathlib, subprocess, sys, time\n"
                "started = pathlib.Path(sys.argv[1])\n"
                "marker = pathlib.Path(sys.argv[2])\n"
                "grandchild = pathlib.Path(sys.argv[3])\n"
                "subprocess.Popen([sys.executable, '-B', str(grandchild), str(marker)])\n"
                "started.write_text(str(time.time()), encoding='utf-8')\n"
                "time.sleep(10)\n",
                encoding='utf-8',
            )

            result = run_bounded_process(
                [sys.executable, '-B', str(parent), str(started), str(marker), str(grandchild)],
                cwd=folder,
                env=dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1'),
                timeout=1.0,
            )

            self.assertTrue(started.is_file(), 'fixture parent did not spawn its child before timeout')
            self.assertTrue(result.timed_out)
            time.sleep(1.1)
            self.assertFalse(marker.exists(), 'a descendant survived after the test runner timed out')


class ReceiptClassificationTests(unittest.TestCase):
    def test_existing_statuses_remain_compatible(self):
        passed = classify_output(0, b'PASS: core contract\n')
        self.assertEqual(passed['status'], 'passed')
        self.assertEqual(passed['skip_count'], 0)
        self.assertIsNone(passed['skip_source'])
        failed = classify_output(1, b'assertion failed\n')
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['outcome'], 'failed')

    def test_all_explicit_skip_is_classified_skipped_with_reasons(self):
        classified = classify_output(0, b'\nSKIP: optional renderer unavailable\nSKIP: native bridge unavailable\n')
        self.assertEqual(classified['status'], 'skipped')
        self.assertEqual(classified['outcome'], 'passed')
        self.assertEqual(classified['skip_count'], 2)
        self.assertEqual(classified['skip_source'], 'skip_markers')
        self.assertTrue(classified['skip_reasons_complete'])
        self.assertEqual(classified['skip_messages'],
                         ['optional renderer unavailable', 'native bridge unavailable'])

    def test_partial_skip_marker_is_structured_instead_of_plain_pass(self):
        classified = classify_output(
            0,
            b'PASS: core contract\nSKIP: optional renderer unavailable\nPASS: final summary\n',
        )
        self.assertEqual(classified['status'], 'passed_with_skips')
        self.assertEqual(classified['outcome'], 'passed')
        self.assertEqual(classified['skip_count'], 1)
        self.assertEqual(classified['skip_source'], 'skip_markers')
        self.assertEqual(classified['skip_messages'], ['optional renderer unavailable'])
        self.assertEqual(classified['skips'], [
            {'line': 2, 'reason': 'optional renderer unavailable', 'source': 'skip_marker',
             'message': 'optional renderer unavailable'},
        ])

    def test_unittest_partial_skip_without_reason_is_unknown(self):
        classified = classify_output(0, b'Ran 64 tests in 12.345s\n\nOK (skipped=1)\n')
        self.assertEqual(classified['status'], 'passed_with_skips')
        self.assertEqual(classified['skip_count'], 1)
        self.assertEqual(classified['skip_source'], 'unittest_summary')
        self.assertFalse(classified['skip_reasons_complete'])
        self.assertEqual(classified['skip_messages'], ['unknown'])
        self.assertEqual(classified['unittest'],
                         {'ran': 64, 'summary': 'OK', 'skipped': 1})

    def test_unittest_all_skipped_is_classified_skipped(self):
        classified = classify_output(0, b'Ran 2 tests in 0.010s\n\nOK (skipped=2)\n')
        self.assertEqual(classified['status'], 'skipped')
        self.assertEqual(classified['outcome'], 'passed')
        self.assertEqual(classified['skip_count'], 2)
        self.assertEqual(classified['skip_source'], 'unittest_summary')
        self.assertEqual(classified['skip_messages'], ['unknown', 'unknown'])

    def test_unittest_skip_reason_is_preserved_when_available(self):
        classified = classify_output(
            0,
            b"test_optional (__main__.Suite.test_optional) ... skipped 'missing renderer'\n"
            b"Ran 1 test in 0.001s\n\nOK (skipped=1)\n",
        )
        self.assertEqual(classified['status'], 'skipped')
        self.assertEqual(classified['skip_count'], 1)
        self.assertTrue(classified['skip_reasons_complete'])
        self.assertEqual(classified['skip_messages'], ['missing renderer'])

    def test_unittest_double_quoted_skip_reason_is_preserved(self):
        classified = classify_output(
            0,
            b'test_link (__main__.Suite.test_link) ... skipped "link fixture unavailable: '
            b"'[WinError 5] denied'\"\n"
            b'Ran 1 test in 0.001s\n\nOK (skipped=1)\n',
        )
        self.assertEqual(classified['status'], 'skipped')
        self.assertTrue(classified['skip_reasons_complete'])
        self.assertEqual(
            classified['skip_messages'],
            ["link fixture unavailable: '[WinError 5] denied'"],
        )

    def test_unittest_failure_with_skips_remains_failed(self):
        classified = classify_output(
            1,
            b'Ran 4 tests in 0.020s\n\nFAILED (failures=1, skipped=2)\n',
        )
        self.assertEqual(classified['status'], 'failed')
        self.assertEqual(classified['outcome'], 'failed')
        self.assertEqual(classified['skip_count'], 2)
        self.assertEqual(classified['skip_source'], 'unittest_summary')
        self.assertEqual(classified['skip_messages'], ['unknown', 'unknown'])

    def test_embedded_words_are_not_skip_markers(self):
        classified = classify_output(0, b'PASS: text says SKIP: inside a sentence\n')
        self.assertEqual(classified['status'], 'passed')
        self.assertEqual(classified['skip_count'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)