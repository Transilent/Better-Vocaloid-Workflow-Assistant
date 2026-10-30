# 简易混音

在任务的“文件与下一步”中选择“制作简易混音”，导入歌声合成软件导出的干声和伴奏，调整平衡及人声处理，试听后导出新的 WAV；确认后可登记为任务 Mixdown 并继续合成视频。输入文件不会被覆盖。处理前试听保留音量和偏移，只旁通 EQ、压缩和混响。试听通过系统默认播放器打开。

首次使用时，在混音界面点击“安装混音组件”。助手会下载 Pedalboard 0.9.26 Windows x64 wheel，核对固定的大小和 SHA-256，解包到 `dependencies/optional/pedalboard/current`，并在隔离子进程中检查效果链；无需修改基础 Python 的 site-packages。下载约 3.5 MiB，安装后的运行文件约 9.1 MiB。需要网络连接和应用目录写权限。

组件固定版本与来源见 `components/pedalboard.json`，许可证及静态依赖见 `components/pedalboard-THIRD_PARTY_NOTICES.md`。现有 v1.0.0 发行包没有此功能。发布前应验证实际构建的更新兼容性与对应源码提供方式；旧更新客户端可能不接受新增文件，应先验证兼容首跳或完整包。
