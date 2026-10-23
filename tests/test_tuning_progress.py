"""Pure timing and stage-message contracts for tuning progress; no worker or window."""
from pathlib import Path
import sys

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from tuning_ui import TuningVisualProgress, tuning_stage_for_message

clock = [100.]


def now():
    return clock[0]


progress = TuningVisualProgress(clock=now)
assert progress.start() == 0
assert progress.running
assert progress.sample() == 0, 'Starting the clock alone must not invent advancement'

clock[0] = 101.; early = progress.sample()
clock[0] = 102.; next = progress.sample()
assert early > 0 and next > early
first_step = next - early

clock[0] = 131.; late_before = progress.sample()
clock[0] = 132.; late_after = progress.sample()
assert late_before >= next and late_after > late_before
assert first_step > late_after - late_before, 'Motion should be faster early and slower later'

clock[0] = 1e9
assert progress.sample() == 95, 'The running visual bar has a strict non-completion cap'
assert progress.sample() == 95

frozen = progress.pause()
clock[0] += 1000
assert progress.sample() == frozen and progress.value == frozen, 'A paused clock must not keep advancing'
assert progress.complete() == 100, 'Only an explicit complete transition reaches the end'
assert progress.reset() == 0
clock[0] += 1000
assert progress.sample() == 0, 'A reset, not-started clock must remain at zero'

assert tuning_stage_for_message('从固定输入快照开始处理') == 'snapshot'
assert tuning_stage_for_message('处理参数来源：reference') == 'source'
assert tuning_stage_for_message('分析参考演唱 · 1 / 7') == 'source'
assert tuning_stage_for_message('叠加所选风格增量') == 'style'
assert tuning_stage_for_message('统一保形加密与保护校验') == 'protection'
assert tuning_stage_for_message('独立调音工程已提交：applied') == 'save'
assert tuning_stage_for_message('正在补全已提交调音工程的回执；不会重复叠加。') == 'save'
assert tuning_stage_for_message('报告待补全') == 'report'
assert tuning_stage_for_message('与阶段无关的日志') is None

source = Path(sys.modules['tuning_ui'].__file__).read_text(encoding='utf-8')
assert '估计进度' not in source and 'estimated progress' not in source.lower()
print('PASS: injected tuning clock is monotonic, fast-then-slow, capped, resettable, and stage text follows actual messages')