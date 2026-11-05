"""Bounded test-process execution and structured receipt classification."""
from dataclasses import dataclass
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time


@dataclass(frozen=True)
class ProcessResult:
    stdout: bytes
    stderr: bytes
    returncode: int | None
    timed_out: bool
    seconds: float


def _terminate_owned_tree(process):
    if os.name != 'nt' and process.pid:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        from process_control import terminate_tree
    except ModuleNotFoundError:
        source_root = Path(__file__).resolve().parents[1] / 'src'
        sys.path.insert(0, str(source_root))
        from process_control import terminate_tree
    terminate_tree(process)


def run_bounded_process(argv, *, cwd, env, timeout):
    """Run one test process and reclaim its descendants before returning."""
    if timeout <= 0:
        raise ValueError('timeout must be positive')
    started = time.monotonic()
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        options = {
            'cwd': cwd,
            'env': env,
            'stdout': stdout_file,
            'stderr': stderr_file,
        }
        if os.name == 'nt':
            options['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        else:
            options['start_new_session'] = True
        process = subprocess.Popen([str(value) for value in argv], **options)
        timed_out = False
        try:
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _terminate_owned_tree(process)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    _terminate_owned_tree(process)
                    process.wait(timeout=10)
        except BaseException:
            if process.poll() is None:
                _terminate_owned_tree(process)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            raise
        stdout_file.flush()
        stderr_file.flush()
        stdout_file.seek(0)
        stderr_file.seek(0)
        return ProcessResult(stdout_file.read(), stderr_file.read(), process.returncode,
                             timed_out, round(time.monotonic() - started, 3))


def _unittest_reason(value):
    match = re.match(r'^.+ \.\.\. skipped (?:\'(.+)\'|"(.+)"|\((.+)\))$', value)
    if not match:
        return None
    return match.group(1) or match.group(2) or match.group(3)


def classify_output(exit_code, output):
    """Keep explicit and unittest skips visible without changing exit semantics."""
    skip_markers = []
    unittest_reasons = []
    pass_markers = []
    other_lines = []
    ran_count = None
    summary_status = None
    unittest_skipped = None

    for line_number, line in enumerate(output.decode('utf-8', errors='replace').splitlines(), 1):
        value = line.strip()
        if not value:
            continue
        ran_match = re.fullmatch(r'Ran (\d+) tests? in .+', value)
        summary_match = re.fullmatch(r'(OK|FAILED) \((.*)\)', value)
        if ran_match:
            ran_count = int(ran_match.group(1))
            continue
        if summary_match:
            summary_status = summary_match.group(1)
            skipped_match = re.search(r'(?:^|, )skipped=(\d+)(?:,|$)', summary_match.group(2))
            if skipped_match:
                unittest_skipped = int(skipped_match.group(1))
            continue
        if value.startswith('SKIP:'):
            skip_markers.append({'line': line_number, 'reason': value[5:].strip(),
                                 'source': 'skip_marker'})
        elif value.startswith('PASS:'):
            pass_markers.append({'line': line_number, 'message': value[5:].strip()})
        else:
            reason = _unittest_reason(value)
            if reason is not None:
                unittest_reasons.append({'line': line_number, 'reason': reason,
                                         'source': 'unittest_summary'})
            else:
                other_lines.append(value)

    summary_count = unittest_skipped or 0
    marker_count = len(skip_markers)
    skip_count = max(summary_count, marker_count)
    reasons = ([item for item in unittest_reasons if item['reason'] != 'unknown']
               if summary_count else [])
    reasons.extend(item for item in skip_markers if item['reason'])
    skips = reasons[:skip_count]
    if len(skips) < skip_count:
        skips.extend({'line': None, 'reason': 'unknown', 'source': 'unittest_summary'}
                     for _ in range(skip_count - len(skips)))
    for item in skips:
        item.setdefault('message', item['reason'])

    if summary_count and marker_count:
        skip_source = 'mixed'
    elif summary_count:
        skip_source = 'unittest_summary'
    elif marker_count:
        skip_source = 'skip_markers'
    else:
        skip_source = None

    unittest_all_skipped = bool(summary_count and ran_count == summary_count)
    marker_all_skipped = bool(marker_count and not pass_markers and not other_lines and not summary_status)
    if exit_code != 0:
        status = 'failed'
        outcome = 'failed'
    elif skip_count and (unittest_all_skipped or marker_all_skipped):
        status = 'skipped'
        outcome = 'passed'
    elif skip_count:
        status = 'passed_with_skips'
        outcome = 'passed'
    else:
        status = 'passed'
        outcome = 'passed'
    return {
        'status': status,
        'outcome': outcome,
        'skip_count': skip_count,
        'skip_source': skip_source,
        'skip_reasons_complete': all(item['reason'] != 'unknown' for item in skips),
        'skips': skips,
        'skip_messages': [item['message'] for item in skips],
        'pass_markers': pass_markers,
        'unittest': {
            'ran': ran_count,
            'summary': summary_status,
            'skipped': unittest_skipped,
        },
    }