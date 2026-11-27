# AMD 显卡（A卡）适配协作开发指南

[开发者总指南](DeveloperGuide.zh-CN.md) · [架构说明](Architecture.zh-CN.md) · [贡献规范](../CONTRIBUTING.zh-CN.md)

本文档为 VWA **AMD 显卡（Radeon 独显 & APU 核显）适配专项**的协作入口，梳理了当前全部 3 套 GPU 推理栈的现状、精确源码触点、已知缺口、可选技术路线与分阶段 PR 计划。

---

## 1. VWA 当前三大 GPU 推理栈全景

VWA 的模型推理全部运行在隔离子进程中，目前涉及 **3 套相互独立的加速栈**，对 AMD 显卡的支持程度各不相同：

| 推理栈 | 覆盖功能 | 当前后端 | AMD 显卡（A卡）现状 | 适配优先级 |
| :--- | :--- | :--- | :--- | :---: |
| **栈 1：ONNX Runtime (`onnxruntime-directml`)** | MDX 伴奏/和声分离、RMVPE 音高提取、GAME 音符检测、HubertFA / TiFA 强制对齐、RomajiASR、PinyinASR、KashiG2P、Qwen3-ASR Encoder | `DmlExecutionProvider` (DXGI / DirectML) | **基础已通，存在细节缺口**：独立 A卡理论上可走 DirectML，但缺少实机回归验证，且 **AMD APU 核显（如 780M/890M）易因专用显存 `< 1 GiB` 被过滤退回 CPU** | **P0** |
| **栈 2：Qwen3-ASR LLM (`llama.cpp` GGUF)** | 中文歌词识别大模型解码器 | `llama_backend` (`auto` / `cpu` / 原生后端 DLL) | **待 A卡实机核验**：需确认 Windows A卡下是否命中 Vulkan / 正确后端、是否存在显存残留或退回 CPU | **P1** |
| **栈 3：可选组件 BS-RoFormer (`pymss` + PyTorch)** | 高精度主唱/和声二次分离 | `torch-2.7.1+cpu` / `torch-2.7.1+cu128`（仅 CPU / NVIDIA CUDA） | **完全空白（最大缺口）**：清单 `components/roformer.json`、安装探测、推理入口与 UI 均仅支持 `cpu` 与 `cuda`，A卡用户目前只能跑 CPU | **P0** |

---

## 2. 精确源码触点地图（按职责分组）

### A. 统一显卡枚举与 ONNX DirectML 路由
| 文件 | 关键符号 / 行区间 | 当前行为与 A卡注意点 |
| :--- | :--- | :--- |
| [dependencies/vocal2midi2/inference/device_utils.py](../dependencies/vocal2midi2/inference/device_utils.py) | `_enumerate_dxgi_adapters()` (L108-170)<br>`select_preferred_gpu_adapter()` (L182-199)<br>`resolve_onnx_providers()` (L205-228) | 通过 `dxgi.dll` 枚举所有硬件显卡。**注意点 1**：L185 目前只读取 `BVWA_DML_ADAPTER`（建议同时兼容 `VWA_DML_ADAPTER`）。**注意点 2**：L194 过滤条件 `dedicated_vram_bytes >= 1 GiB` 会把部分 BIOS 分配 512 MB 专用显存但有大共享显存的 AMD 核显（APU）排除掉！**注意点 3**：`DxgiAdapterInfo` 可补充暴露 `vendor_id`（AMD=`0x1002`、NVIDIA=`0x10DE`、Intel=`0x8086`），方便上层区分厂商策略。 |
| [src/runtime_devices.py](../src/runtime_devices.py) | `prepare_environment()` (L31-38)<br>`adapters()` (L51-52)<br>`providers()` (L55-59) | 所有 ONNX 引擎共用的适配器与线程配置桥；同时写入 `VWA_DML_ADAPTER` / `BVWA_DML_ADAPTER`。 |
| [src/mdx.py](../src/mdx.py) | `separate()` (L84-94) | 创建 `ort.InferenceSession`；已关闭 `enable_mem_pattern = False` 并使用 `ORT_SEQUENTIAL`（对 DirectML 非常重要，切勿重新开启）。 |
| [src/error_recovery.py](../src/error_recovery.py) | DirectML / CUDA 错误分类与 CPU 自动回退 | 需补充 AMD DirectML / ROCm / HIP 常见 OOM、驱动重置（DXGI_ERROR_DEVICE_REMOVED / HUNG）关键词识别与友好提示。 |

### B. Qwen3-ASR (`llama.cpp` + ONNX Encoder)
| 文件 | 关键符号 | 当前行为与 A卡注意点 |
| :--- | :--- | :--- |
| [dependencies/vocal2midi2/inference/qwen3asr_dml/runtime.py](../dependencies/vocal2midi2/inference/qwen3asr_dml/runtime.py) | `resolve_llama_backend()` (L106-114) | `device == "cpu"` 时强制 `"cpu"`，否则传 `"auto"`。需排查同目录 `llama.py` 与 `asr_worker.py` 在 AMD 显卡下实际加载的后端（Vulkan vs CPU）及显存释放表现。 |
| [src/midi_bridge.py](../src/midi_bridge.py) & [src/v2m_runtime.py](../src/v2m_runtime.py) | `midi_device` 传递与进程隔离 | 中文 GPU 任务默认按声部独立子进程运行，防止下一轮 ASR 与上一轮模型争抢显存。 |

### C. 可选 BS-RoFormer / PyTorch 运行库与界面
| 文件 | 关键符号 | 当前行为与 A卡注意点 |
| :--- | :--- | :--- |
| [components/roformer.json](../components/roformer.json) | `torch_wheels` (L78-95) | 目前仅有 `cpu` 与 `cuda` (`torch-2.7.1+cu128`) 两个运行库条目及对应 `install_bytes`。 |
| [src/optional_components.py](../src/optional_components.py) | `probe()` (L356-376)<br>`install()` (L379+) | `probe()` 目前硬编码 `torch.cuda.is_available()` 并执行 `device='cuda'` 矩阵自检；需抽象为按目标后端验证（如 `rocm` / `directml` / `cuda` / `cpu`）。 |
| [src/roformer.py](../src/roformer.py) | `separate_vocals()` (L11-53) | L32 硬编码 `if device == 'cuda' and not torch.cuda.is_available()`；L39 将 `device` 直接透传给 `pymss.MSSeparator`。 |
| [src/download_sources.py](../src/download_sources.py) | `PYTORCH_PREFIX` / `PYTORCH_VERIFIED_WHEELS` | 若新增 PyTorch ROCm / DirectML wheel，需在此登记白名单与国内镜像路由（如支持）。 |
| [src/component_operations.py](../src/component_operations.py) / [src/component_health.py](../src/component_health.py) | 可选组件安装与状态校验 | 校验已安装运行库类型（目前仅识别 `cpu` / `cuda`）。 |
| [src/pipeline.py](../src/pipeline.py) / [src/preparation_commands.py](../src/preparation_commands.py) / [src/workflow.py](../src/workflow.py) / [src/vwa_config.py](../src/vwa_config.py) | `separation_device` / `midi_device` 配置与传递 | 统一扩展设备选项枚举与配置合法性校验（`vwa_config.CONFIG_CHOICES`）。 |
| [src/quick_ui/ComponentsPage.qml](../src/quick_ui/ComponentsPage.qml) & [src/quick_ui/PreparationPage.qml](../src/quick_ui/PreparationPage.qml) | 组件安装按钮 & 设备选择 UI | 目前按钮/文案直接写 `CUDA`；A卡适配后需根据本机探测的显卡厂商（DXGI `VendorId`）动态高亮推荐对应后端，并补齐中/英/日三语翻译。 |

---

## 3. BS-RoFormer A卡加速：三条技术路线对比

在开始写代码前，建议两人结合目标用户群的显卡分布（RX 6000 / RX 7000 / RX 9000 / 780M 核显）先拍板路线：

| 路线 | 技术方案 | 优点 | 缺点 / 风险 |
| :--- | :--- | :--- | :--- |
| **路线 A（推荐通用兜底）** | **BS-RoFormer 导出 ONNX + 现有 `onnxruntime-directml`**（或在 `roformer.py` 内支持 ONNX/DirectML 推理引擎） | 1. **覆盖全系 AMD 独显 + 核显 + Intel Arc + NVIDIA**<br>2. 复用基础包已有的 `onnxruntime-directml`，**无需用户额外下载 2-3 GB PyTorch GPU 运行库**<br>3. 架构与现有 `mdx.py` 完全一致 | 1. 需验证 BS-RoFormer STFT/ISTFT 算子转 ONNX 后的精度误差（与 PyTorch 原输出对比 SNR / max diff）<br>2. DirectML 吞吐低于原生 ROCm/CUDA，但仍远快于 CPU |
| **路线 B（高端 A卡高性能）** | **PyTorch on ROCm for Windows（HIP / TheRock wheels）** | 1. 在支持的 RDNA3/4 显卡上速度接近原生 CUDA<br>2. PyTorch API 层面仍映射到 `torch.cuda` / HIP，`pymss` 改动最小 | 1. 仅支持部分新一代 AMD 显卡（老卡如 RX 5000/6000 覆盖不全）<br>2. Wheel 体积巨大（数 GB），且对 Windows Adrenalin 驱动版本较敏感 |
| **路线 C（折中过渡）** | **`torch-directml`** | PyTorch 生态直连 DirectML | `torch-directml` 社区维护停滞（长期滞后于 Python 3.12 / PyTorch 2.7），存在版本锁死与缺失算子风险，**不推荐作为主线** |

> **建议组合策略**：优先补齐 **ONNX DirectML 基础稳定性 + APU 核显支持**（让所有 A卡用户立刻享受 MDX + 全套 Vocal2Midi 加速）；BS-RoFormer 则推荐先抽离独立的 **设备能力探测模块（如 `src/gpu_backend.py`）**，再按实际测试结果决定优先落地 **路线 A（ONNX/DirectML 全覆盖）** 或 **路线 B（ROCm Windows 可选包）**，两者在抽象层上互不冲突、可并存。

---

## 4. 推荐协作分工与 PR 拆分节奏

为避免直接修改大型旧文件触碰 800 行棘轮或造成合并冲突，建议按 **4 个小而清晰的 PR** 推进（全部基于 `dev/chorus-rebuild-20261123` 创建特性分支，例如 `feat/amd-gpu-dxgi`）：

1. **PR 1 · DXGI 厂商探测与 AMD DirectML / APU 核显修复**
   - 在 `device_utils.py` 中补充 `vendor_id`（识别 AMD `0x1002` / NVIDIA `0x10DE` / Intel `0x8086`）、同时读取 `VWA_DML_ADAPTER` 与 `BVWA_DML_ADAPTER`。
   - 解决 AMD APU 核显（专用显存 512 MB + 共享显存 >= 4 GiB）被误判跳过的问题。
   - 在实机 AMD 显卡上跑通 MDX 分离 + Vocal2Midi（中日英）+ RMVPE/TiFA/HubertFA，记录耗时、显存占用与输出校验结果。
2. **PR 2 · 独立设备能力抽象层（`src/gpu_backend.py`）与错误恢复增强**
   - 新建纯 Python 模块 `src/gpu_backend.py`（零 Qt 依赖、`< 300` 行）：统一汇总本机 GPU 厂商、DirectML 适配器列表、CUDA / ROCm 可用状态与推荐后端。
   - 在 `src/error_recovery.py` 中补充 AMD DirectML / HIP 典型故障识别与自动降级 CPU 策略。
   - 配套纯 Python 模拟单测（Mock DXGI 返回 AMD/NVIDIA/APU 多种硬件组合，无需真实 A卡也能在 CI 中 100% 跑通）。
3. **PR 3 · BS-RoFormer A卡推理后端与组件清单**
   - 按敲定的路线（ONNX DirectML 或 ROCm wheel）接入 `components/roformer.json`、`src/optional_components.py`、`src/roformer.py`、`src/component_operations.py`。
   - 保持现有 CPU / CUDA 用户的清单、校验与回滚路径 100% 向后兼容。
4. **PR 4 · 界面适配、诊断摘要与三语翻译**
   - 更新 `ComponentsPage.qml`、`PreparationPage.qml` 与诊断导出信息，展示本机检测到的显卡型号与推荐加速后端。
   - 在 `assets/i18n/additions.json` 中补齐全部新增中文文案的 `en` / `ja` 译文，跑通 `extract_i18n.py --check --max-missing-en 0 --max-missing-ja 0` 与 `test_quick_desktop.py`。

---

## 5. A卡实机验收核对清单

提交涉及真实硬件加速的 PR 时，建议附上以下实测记录（在 PR 模板的“验证”一栏填报）：

- **硬件与驱动信息**：显卡型号（如 RX 7800 XT / RX 6700 XT / Radeon 780M）、Windows 版本、AMD Adrenalin 驱动版本号。
- **MDX 分离实测**：1 首 3–4 分钟 44.1 kHz 立体声歌曲，DirectML vs CPU 耗时对比、输出无 NaN/爆音/断流、峰值显存占用。
- **Vocal2Midi 全流程实测**：中文（Qwen3-ASR）与日语（RomajiASR + HubertFA/TiFA）各 1 首，确认实际命中的 ExecutionProvider / `llama_backend` 日志、无中途掉驱动或 OOM。
- **BS-RoFormer 实测**（若改动栈 3）：与 CPU 基准输出的数值误差对比（RMS / 最大绝对差）及人耳对比试听结论。
- **异常回退验证**：人为指定不可用设备或模拟显存不足时，能干净提示并可一键回退 CPU，不残留僵尸子进程。
