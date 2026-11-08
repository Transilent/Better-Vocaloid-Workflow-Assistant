# 原生连续试听开发接口

本接口用于验证下一代试听服务，目前未作为 GUI 的默认播放器。它由常驻四轨 PRE-LIMITER 渲染器、工作线程会话和 C 原生输出组成；源准备后，播放及参数更新不再通过生成短 WAV 交接音频。

## 组成与边界

- `PreLimiterMixSession` 复用当前混音的请求解析、源解码缓存、路由及 EQ/压缩/混响处理核。固定双声道设备总线，mono 源按现有 FFmpeg 转换规则上混；声源、顺序及解码器身份在会话中固定。
- `RealtimeAuditionSession` 串行执行 DSP 和原生输出控制，使用有界队列与版本化命令。参数的路径/文件校验通过 `prepare_parameters` 在控制侧完成，`apply_parameters` 在供音线程应用。音量、声像、send、mute/solo、width 不重建效果；结构变化会按有限历史重新预热。
- `NativeOutput` 使用固定 miniaudio 0.11.26 的 WASAPI shared 输出。C 回调只消费 PCM 和更新计数，不进入 Python。WASAPI 对象必须在创建它的线程控制和关闭，因此会话应接收输出工厂；null 测试输出允许受锁保护的独立模拟消费者。
- 渲染器明确输出 PRE-LIMITER，可能超过最终导出的峰值限制。会话拒绝超过当前安全峰值的块，未用另一种 clipping/limiter 冒充原有 FFmpeg `alimiter`。在共享限制器、声音与设备恢复通过验收前，保留现有默认服务。

## 工作线程用法

先在服务工作进程中加载已有固定 Pedalboard 组件和准备声源，再创建会话。不能从音频回调调用这些 Python 接口。

```python
renderer = PreLimiterMixSession(request, cache_dir=cache_dir,
                               pedalboard=pedalboard, ffmpeg=ffmpeg_path)

def create_output():
    return NativeOutput(dll_path, sample_rate=renderer.sample_rate,
                        channels=renderer.channels, capacity_frames=4096,
                        period_frames=256, mode="wasapi")

with RealtimeAuditionSession(renderer, create_output, block_frames=512) as session:
    seek_id = session.seek(target_frame, autoplay=True)
    revision = session.set_parameters(new_request)
    state = session.snapshot()
    session.pause()
```

会话每次接受定位时清理旧代队列并使已准备的旧块失效。播放/暂停保留命令顺序；同批定位和参数分别选择最后一个。停止定位到 0 并保持暂停；关闭有明确超时，不自动创建第二个输出恢复播放。

DSP 块采用固定量子，至少 64 帧；原生设备回调长度独立。Pedalboard 0.9.26 对双声道的 1–2 帧输入存在通道推断歧义，因此渲染器拒绝非终块的 1–3 帧请求；最终触及 `duration_frames` 的余块才可安全补零。极短历史无法安全预热时在 `last_warmup` 中明确标记；有限预热不代表从头播放的完整效果历史。

参数计划只属于生成它的会话，不可反序列化后跨会话复用。源准备身份绑定路径、文件设备/inode/大小/mtime、解码器和双声道布局；文件变更需要新会话。预设仍保存用户参数，不保存缓存路径、DLL 地址或设备 period。

## 构建与复验

使用已有 GCC，不安装编译器或修改应用运行库。脚本要求显式指定或找到本机已有的固定 header；校验版本、源码/许可证摘要，输出 DLL、C 测试和构建回执。源码源头是 miniaudio tag `0.11.26`、提交 `877ea4a7c2601c7b0f2a44e83dfac42e6470fea6`。SHA 固定值以 `tools/Build-MixdownNative.ps1` 为准，许可证为 Public Domain 或 MIT No Attribution 二选一。

```powershell
.\tools\Build-MixdownNative.ps1 -MiniaudioHeaderPath <固定头文件路径> `
  -OutputDirectory <本轮工作目录> -RunTests -ProbeWasapi

python -B .\tools\probe_mixdown_realtime.py --dll <构建DLL> `
  --packages <已有Pedalboard包目录> --ffmpeg <已有FFmpeg> `
  --out <新的报告目录> --mode all --legacy-baseline
```

探针始终静音：实际计算四轨和效果，但给设备提交零值 PCM。支持 `--blocks` 扫描量子，`--soak-seconds` 做最长 120 秒的静音压力验证；null 模式由 Python 节奏模拟设备，不作为实际硬件延迟证据。已有报告目录拒绝覆盖。

原生 C null 测试验证采样、部分写、代次、连续性拒绝与零填充。标准 Python 回归登记为 `test_mixdown_native_output.py`、`test_mixdown_realtime_renderer.py` 和 `test_mixdown_realtime_session.py`。真实组件和实际设备检查分别通过 `VWA_MIX_TEST_PEDALBOARD_PATH`、`VWA_MIX_TEST_FFMPEG_PATH`、`VWA_TEST_WASAPI=1` 启用；缺少能力应明确报告，不能把跳过当成设备通过。

## 时间与设备信息

`media_frame` 表示原生回调已提交的媒体帧，排除暂停/欠载的补零；它不是声学播放位置。原始 `IAudioClock` 的 position 只能除以其 frequency 换算秒，不可假定 frequency 等于采样率。WASAPI clock 的接口定义来自 Windows SDK，不手抄 IID 或 vtable。

seek 报告 CurrentPadding 的 before/after-stop/after-restart，以及时钟 reset 的实际查询结果。只有查询成功才提供证据；没有硬件时钟时明确降级。设备时钟、已提交媒体帧、系统缓冲和耳机/扬声器声学延迟仍需要分别解释。

性能探针使用高精度 `perf_counter`。参数和定位统计是“控制请求至原生回调提交相应媒体”，包含排队和处理，但不包含完整声学延迟。旧 12 秒 WAV 发布计时包含整段渲染/分析，与分块提交并非同一工作量，不能直接算作端到端提速倍数。
