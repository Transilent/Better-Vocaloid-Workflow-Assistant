# 下载来源

[English](DownloadSources.en.md)

在 **设置与更新 → 下载设置** 中选择来源，也可在 **模型与组件** 安装前选择。程序更新重启进入迁移窗口后，会保留选中的下载方式。

- **自动选择**：优先官方直连，失败后尝试支持的备用来源。
- **官方源直连**：使用原始服务地址。
- **优先加速源**：按文件来源选择支持的加速站，并保留直连回退。
- **指定镜像**：只用于对应来源，失败后直连。其他来源的文件保持直连；例如选择 TUNA PyPI 不会把 Qwen 模型发给 TUNA。

## 可用来源

| 来源 | 适用文件 |
| --- | --- |
| GitProxy | GitHub 公开 Release、raw 和 archive 文件 |
| GH-Proxy | GitHub 公开 Release、raw 和 archive 文件 |
| ghfast.top | GitHub 公开 Release、raw 和 archive 文件 |
| HF-Mirror | Hugging Face 公开 resolve 文件，包括现用中文 Qwen 权重 |
| TUNA PyPI | files.pythonhosted.org/packages 下已固定版本和哈希的 wheel |
| 阿里云 PyPI | files.pythonhosted.org/packages 下已固定版本和哈希的 wheel |
| SJTU PyTorch | 现用 Windows Python 3.12 的 torch 2.7.1 CPU、cu128 两个 wheel |

GitHub 加速仅适用于明确的公开文件路径。API 元数据、平台登录和上传、B站签名媒体及网易云请求使用原始服务。第三方加速请求不附加 Cookie、令牌或签名参数；GitHub 上传保持直连。

PyPI 镜像使用同一个已固定的包文件，不重新解析或升级版本。SJTU 路由只覆盖上述两个 wheel 文件名，其他 PyTorch 版本或通道保留原地址。PyPI 镜像不代理任意软件索引或 API。

## 校验、取消和恢复

组件和更新安装沿用固定字节数与 SHA-256 校验，加速站不能选择安装哪个版本。原有下载器保留断点续传、取消和来源回退。小型兼容运行库安装脚本也可选择下载来源，但不具备桌面下载器的全部多连接和 Range 能力。

在 **模型与组件 → 中文歌词识别** 下载未预装的 Qwen 权重。卡片显示大小、已保留下载、进度和取消入口。文件齐全后，**校验 / 修复** 会执行可取消的完整哈希检查，并修复缺失或损坏文件。正常刷新只快速检查文件存在性和大小，不代表重新完成完整 SHA-256 校验。日语识别和纯音符提取不依赖中文权重。

自动更新仅在启动时进行。安装更新会等待处理、组件安装、缓存管理和发布会话等操作结束。最终发布仍由用户在平台网页确认。

## 命令行兼容

共享来源 ID 为 `auto`、`direct`、`mirror`、`gitproxy`、`gh-proxy`、`ghfast`、`hf-mirror`、`pypi-tuna`、`pypi-aliyun`、`pytorch-sjtu`。

可选组件命令支持 `--download-source <ID>`，运行库恢复脚本支持 `-DownloadSource <ID>`。新版迁移工具接受全部十项；当 GitHub 更新使用了只适用于组件的来源时，会直连下载，不会因选项不适用而拒绝启动。旧版兼容窗口可能保留中文显示，完成升级后可在设置中选择语言。
