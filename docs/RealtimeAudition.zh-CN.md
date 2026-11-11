# 原生连续试听开发接口

混音台只有一条原生连续试听链路。源准备后，参数和 A/B 变更通过持续 PCM 生效，不生成试听 WAV。旧分段渲染、双播放器交叉淡化、QtMultimedia 和系统播放器回退已移除；组件缺失或校验失败时明确显示不可用。切换页面或最小化保留同一会话、位置与播放意图，实际关闭应用时才释放服务。人工整曲听感、多声卡和真实热拔插仍需分别验收。

曲线编辑和播放条拥有独立空间：常规桌面布局为左侧轨道区、右侧较宽的分析区；“放大曲线”暂时隐藏轨道与导出区，返回后保留原值。播放条固定在页面底部，进度条独占整行，滚动轨道和设置不会把它带走。导出设置默认收起，预设和轨道区可独立滚动；界面放大时按钮可换行。

简易混音页内，空格切换播放/暂停，长按只触发一次；编辑文字、数值、下拉选择以及打开弹窗时保留控件自己的键盘行为，其他页面不响应。播放按钮提供快捷键提示。

GR 与混响的实时读数使用固定尺寸的绘制条，数值最多每100ms更新一次，不随文本宽度反复排版。GR 同时显示人声共享压缩和总线限制读数，历史图可选择其一；每100ms时间桶保留最大减益，30秒最多约301点，绘图合并刷新，隐藏时不调度重绘。混响发送/返回和湿声能量占比实时更新，单位脉冲EDC是静态图，仅在测量、尺寸、字体或主题改变后重建；不跟随歌曲播放位置。轨道电平映射不在GUI刷新路径解析或查询文件，隐藏的EQ页暂存最新频谱，返回时显示该帧。

## 组成与边界

- 简易混音的界面、参数快照、预设、试听与导出职责见[整体架构](MixdownArchitecture.zh-CN.md)。`MixdownPreviewController`维护输入身份、待提交编辑与本地定位手势；`mixdown_realtime_core`维护播放意图、媒体位置和命令确认，`QuickMixdownPlayer`提供 Qt 信号和计时。
- `PreLimiterMixSession` 复用当前混音的请求解析、源解码缓存、路由及 EQ/压缩/混响处理核。固定双声道设备总线，mono 源按现有 FFmpeg 转换规则上混；声源、顺序及解码器身份在会话中固定。
- `RealtimeAuditionSession` 串行执行 DSP 和原生输出控制，使用有界队列与版本化命令。参数的路径/文件校验通过 `prepare_parameters` 在控制侧完成，`apply_parameters` 在供音线程应用。音量、声像、send、mute/solo、width 不重建效果；结构变化会按有限历史重新预热。
- 当前 Windows/Python 运行库中，`Event.wait(0.002)` 实测会等待约 15.6 ms；供音线程使用短 `time.sleep` 等待并保留命令唤醒标记，避免粗粒度等待耗尽队列。遥测复制仍隔离嵌套状态，但数值历史列表不再递归深拷贝。该调整未修改系统的持久计时设置。
- `NativeOutput` 使用固定 miniaudio 0.11.26 的 WASAPI shared 输出。C 回调只消费 PCM 和更新计数，不进入 Python。WASAPI 对象必须在创建它的线程控制和关闭，因此会话应接收输出工厂；null 测试输出允许受锁保护的独立模拟消费者。
- `LimitedMixSession` 封装 PRE-LIMITER 图和独立 `vwa_limiter.dll`，限制器状态跨块保持，前视补偿及曲尾排空后保持媒体帧数。`mixdown_limiter_spec` 统一导出和连续试听的 -1 dBFS、5 ms attack、100 ms release、level=false、asc=false 规则；流式实现来自 FFmpeg n7.1，独立 LGPL-2.1-or-later 组件。PRE-LIMITER 仅用于诊断，会话仍拒绝非有限或越界的最终 PCM。
- `MixdownRealtimeService` 使用独立的 `mixdown_transport.ResidentProcessClient`，负责进程传输、运行库加载与退出，使用独立会话代次、最多 64 条控制及单槽最新遥测。`mixdown_sources` 提供源缓存及共享 DSP 构造。播放器在 GUI 线程轮询，后台启动、发指令和关闭；只有匹配最新定位的实际回执才放开进度条目标。

## 工作线程用法

先在服务工作进程中加载已有固定 Pedalboard 组件和准备声源，再创建会话。不能从音频回调调用这些 Python 接口。

```python
renderer = LimitedMixSession(request, cache_dir=cache_dir, dll_path=dll_path,
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

会话每次接受定位时清理旧代队列并使已准备的旧块失效。播放/暂停保留命令顺序；同批定位和参数分别选择最后一个。停止定位到 0 并保持暂停。设备异常停止或中断会终止会话；回调两秒无进展也视为故障。关闭后由用户点击播放重新建立输出，不自动播放或偷偷创建第二个设备。

定位重新预热有限效果历史和 100 ms 限制器释放历史，不能宣称与从曲首连续播放的所有状态完全等价。效果结构变更最多回放 0.5 秒历史，预热期间不生成遥测；混响脉冲测量在控制侧缓存，避免重复测量阻塞供音。整曲在源尾音预算后排空限制器，末尾不丢前视帧。原生输出接收绑定定位代次的结束帧，结束后的补零不记为欠载；定位清除结束标记，真实断供仍计数。循环沿用所选试听范围，由控制回执驱动回绕，目前不承诺采样级无缝循环。A/B 共享当前媒体位置，新的参数已经提交后才切换选中状态。

默认队列按实际声卡的三个 period 换算成源采样帧，并向 DSP 块对齐，同时受固定容量约束。设备 period 与 DSP 块分开处理，避免把设备缓冲时长误当成固定的源帧数；显式队列参数仍用于部分写入等测试与设备调优。本机两个 period 的候选虽通过独立压力测试，但在完整窗口中改参/导出时发生欠载，因此没有采用。

## 实测数据接线

会话按已提交的媒体帧选择有界遥测块，`snapshot.telemetry` 的 schema 为 `vwa.audio_mixdown.realtime-telemetry/v1`，包含独立的 `media_frame`、`parameter_revision`、`history_epoch` 及来源。不能用生产线程提前算出的后续音频替换当前显示；尚未提交或定位待确认时明确无当前测量。

- `tracks` 与 `bus` 提供实际音频块 RMS/采样峰值，各轨 mute/solo 直接反映在测量中。总线测量包含输出音量；不是标准 LUFS 或真峰值。
- `spectrum` 是歌声总线 EQ 前后真实 FFT，频率为对数轴；右轴 dBFS 与左轴 EQ 增益 dB 分开。Quick 曲线从当前遥测读取 A/B 频谱。
- 压缩与限制 GR 来自对齐音频块处理前后的 RMS 差，属于测量推导的估计值；共享歌声总线 GR 不冒充每个插件内部读数。
- 混响显示实际发送/返回 RMS 与混合分量能量比例。空间衰减使用独立后台单位脉冲测量，未测得时显示未测量；它不是歌曲响度随时间的轨迹。暂停时后台测量完成也必须刷新。
- 电平与 GR 使用当前遥测，缺失值为未知。来源、代次、参数或定位变化会切断旧 GR 历史。

DSP 块采用固定量子，至少 64 帧；原生设备回调长度独立。Pedalboard 0.9.26 对双声道的 1–2 帧输入存在通道推断歧义，因此渲染器拒绝非终块的 1–3 帧请求；最终触及 `duration_frames` 的余块才可安全补零。极短历史无法安全预热时在 `last_warmup` 中明确标记；有限预热不代表从头播放的完整效果历史。

参数计划只属于生成它的会话，不可反序列化后跨会话复用。源准备身份绑定路径、文件设备/inode/大小/mtime、解码器和双声道布局；文件变更需要新会话。预设仍保存用户参数，不保存缓存路径、DLL 地址或设备 period。

## 构建与复验

使用已有 GCC，不安装编译器或修改应用运行库。脚本要求显式指定或找到本机已有的固定 header；校验版本、源码/许可证摘要，输出 DLL、C 测试和构建回执。源码源头是 miniaudio tag `0.11.26`、提交 `877ea4a7c2601c7b0f2a44e83dfac42e6470fea6`。SHA 固定值以 `tools/Build-MixdownNative.ps1` 为准，许可证为 Public Domain 或 MIT No Attribution 二选一。

```powershell
.\tools\Build-MixdownNative.ps1 -MiniaudioHeaderPath <固定头文件路径> `
  -OutputDirectory <本轮工作目录> -RunTests -ProbeWasapi -InstallForDevelopment

python -B .\tools\probe_mixdown_realtime.py --dll <构建DLL> `
  --packages <已有Pedalboard包目录> --ffmpeg <已有FFmpeg> `
  --out <新的报告目录> --mode wasapi --blocks 512 --limited --soak-seconds 30
```

构建生成独立输出 DLL 与限制器 DLL。`-InstallForDevelopment` 仅将它们和身份清单放入当前开发 checkout 的 `components/native-audio`，不修改用户安装。GUI 校验两个 DLL 摘要后才启用连续试听入口。`VWA_MIXDOWN_NATIVE_DLL` 可指定开发输出 DLL，限制器必须在同目录。发行清单列明这两个 DLL、身份清单、限制器修改源码及许可证；新 checkout 需先构建，不能把未构建组件冒充可发行包。

探针始终静音：实际计算四轨、效果和限制器，但给设备提交零值 PCM。支持 `--blocks` 扫描量子，`--soak-seconds` 做最长 120 秒的静音压力验证；null 模式由 Python 节奏模拟设备，不作为实际硬件延迟证据。已有报告目录拒绝覆盖。服务实际设备的无人值守验证必须显式设置 `VWA_REALTIME_SILENT_PROBE=1`，普通播放不设置该变量。

限制器可以脱离输出组件独立重建和替换，应用通过 ctypes 动态加载，不静态链接进 Python。使用现有 C11 编译器，例如在源码根执行：

```powershell
gcc -std=c11 -O2 -DVWA_LIMITER_BUILD_DLL -shared src/native/vwa_limiter.c -o components/native-audio/vwa_limiter.dll -static-libgcc
```

GUI 的组件清单摘要也需更新为新限制器 DLL 的 SHA-256（`limiter_dll_sha256`），或用 `VWA_MIXDOWN_NATIVE_DLL` 指定同目录输出组件进行开发。来源、修改范围及完整许可见 `components/ffmpeg-alimiter-NOTICE.txt` 和 `components/ffmpeg-alimiter-LICENSE.txt`。

原生 C null 测试验证采样、部分写、代次、连续性拒绝与零填充。标准 Python 回归登记为 `test_mixdown_native_output.py`、`test_mixdown_realtime_renderer.py` 和 `test_mixdown_realtime_session.py`。真实组件和实际设备检查分别通过 `VWA_MIX_TEST_PEDALBOARD_PATH`、`VWA_MIX_TEST_FFMPEG_PATH`、`VWA_TEST_WASAPI=1` 启用；缺少能力应明确报告，不能把跳过当成设备通过。

## 时间与设备信息

`media_frame` 表示原生回调已提交的媒体帧，排除暂停/欠载的补零；它不是声学播放位置。原始 `IAudioClock` 的 position 只能除以其 frequency 换算秒，不可假定 frequency 等于采样率。WASAPI clock 的接口定义来自 Windows SDK，不手抄 IID 或 vtable。

seek 报告 CurrentPadding 的 before/after-stop/after-restart，以及时钟 reset 的实际查询结果。只有查询成功才提供证据；没有硬件时钟时明确降级。设备时钟、已提交媒体帧、系统缓冲和耳机/扬声器声学延迟仍需要分别解释。

性能探针使用高精度 `perf_counter`。参数和定位统计是“控制请求至原生回调提交相应媒体”，包含排队和处理，但不包含完整声学延迟。旧 12 秒 WAV 发布计时包含整段渲染/分析，与分块提交并非同一工作量，不能直接算作端到端提速倍数。
