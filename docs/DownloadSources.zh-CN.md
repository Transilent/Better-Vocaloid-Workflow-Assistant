# 下载来源

[English](DownloadSources.en.md)

在 **模型与组件** 选中要安装的项目，再选 **下载来源**。每个组件各自记住选择。**设置与支持 → 下载与存储** 单独控制程序更新的来源，并行下载数由两者共用。

| 组件 | 可选来源 |
| --- | --- |
| 内置 Karaoke 2、日语识别 | 随安装包提供，不需要选择下载站。文件缺失时恢复对应安装包。 |
| BS‑RoFormer（CPU / CUDA） | **魔搭权重 + GitHub 配置** 或 **GitHub Release（权重和配置）**。清华、中科大、阿里云 PyPI 仅用于 Python 依赖，交大 PyTorch 仅用于对应的 CPU / CUDA 运行库。 |
| 中文歌词识别（Qwen3‑ASR） | Hugging Face 官方源、HF‑Mirror。HF‑Mirror 可能跳回 Hugging Face；目前没有通过完整核验的独立来源。 |
| 参考演唱音色（WORLD） | 官方 PyPI、清华、中科大、阿里云 PyPI。 |
| 混音 Pedalboard（命令行安装） | 官方 PyPI、清华、中科大、阿里云 PyPI。图形界面仍可选择已下载的 wheel 离线安装。 |

**自动选择**先访问官方地址，失败时切到适用的备用源；**官方源直连**只用官方地址。指定站点只改变它能提供的文件；其他文件各走自己的来源。BS‑RoFormer 如果连不上 Hugging Face，选 **魔搭权重 + GitHub 配置** 或 **GitHub Release（权重和配置）**，两种模式都不访问 Hugging Face。魔搭没有匹配的配置文件；配置优先从 GitHub Release 获取，必要时走已校验的 GitHub 备用地址。无法访问 Hugging Face 又需要中文识别时，可选用自带 Qwen 权重的普通版安装包。

模型与运行库使用同一版本、大小和 SHA‑256。下载可取消、继续，完整校验通过后才会启用文件。下载字节数达到总量后，状态会切换到解包或校验，直到组件可用。下载报错时，从底部或「设置与支持 → 支持与日志」打开本机诊断并复制摘要，注明组件及 CPU / CUDA；无需提交模型或完整配置。摘要会记录失败文件、站点和跳转域名、错误码与已保留的字节数。**中文歌词识别**的正常刷新只检查文件存在及大小，完整校验需从组件卡片单独执行。

程序更新仍支持自动、官方、优先加速及 GitProxy、GH‑Proxy、ghfast.top。第三方加速仅用于公开文件，登录、上传及带签名的请求不改道。

BS‑RoFormer 与 Pedalboard 的命令行安装支持 `--download-source`。BS 新增 ID `modelscope`、`github-release`；PyPI 依赖新增 `pypi-ustc`。已有 `mirror` 命令仍可用，图形界面可直接选择实际站点。
