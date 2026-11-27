# VWA 开发者快速上手指南

[架构说明](Architecture.zh-CN.md) · [AMD 显卡（A卡）适配指南](AmdGpuAdaptation.zh-CN.md) · [贡献规范](../CONTRIBUTING.zh-CN.md) · [验证与限制](Verification.zh-CN.md)

本指南面向参与 VWA（Vocaloid Workflow Assistant）功能开发、硬件适配（如 AMD 显卡适配）或缺陷修复的协作者，帮助你快速理解仓库布局、本地运行环境、测试分层与 CI 强制护栏。

---

## 1. 仓库目录结构

```text
.
├── src/                        # 【唯一手写源码目录】所有 Python 业务逻辑与 Qt Quick 界面
│   ├── quick_ui/               # Qt 6 QML 界面文件（11 个工作区 + 公共组件）
│   └── native/                 # C/C++ 原生音频 DLL 源码（CMakeLists.txt + CTest）
├── packaging/                  # 构建、清单与打包元数据
│   ├── source-files.json       # 仓库源码清单（新增文件需同步登记）
│   ├── portable-files.json     # 便携发行包映射清单（所有 src/ 非 native 文件必须登记）
│   ├── config.example.json     # 默认配置模板
│   ├── installer/VWA.iss       # Inno Setup 安装程序脚本
│   └── winget/                 # WinGet 清单模板
├── components/                 # 可选组件清单（如 roformer.json、native-audio/）
├── dependencies/vocal2midi2/   # 集成的 headless 扒谱/对齐后端源码（含 device_utils.py、qwen3asr_dml/）
├── vendor/                     # 固定的上游工程转换库源码（LibreSVIP 等）
├── assets/i18n/                # 多语言词库（additions.json：简体中文 / English / 日本語）
├── tests/                      # 自动化回归测试套件（run_tests.py + conftest.py + L0/L1 脚本）
├── tools/                      # 构建、准备、校验与 i18n 审计工具脚本
├── docs/                       # 用户指南、架构说明与专题开发文档
├── .github/workflows/ci.yml    # GitHub Actions 自动化 CI（L0 护栏 + 原生 CMake/CTest）
├── pyproject.toml              # Ruff / Pytest 工程配置
└── CMakePresets.json           # MinGW / MSVC 原生编译预设
```

> **为什么仓库根目录也会出现 `*.py` 和 `quick_ui/`？**  
> 根目录的 `*.py` 与 `quick_ui/` 是由 `tools/Prepare-Source.ps1` 从 `src/` 自动生成的开发镜像副本，已在 `.gitignore` 中忽略。**所有修改必须只在 `src/` 中进行**，改完后运行 `tools/Prepare-Source.ps1` 同步，严禁直接手改或提交根目录生成副本。

> **为什么 `src/` 保持扁平模块结构？**  
> VWA 2.x 便携版和在线增量更新协议（`packaging/portable-files.json` 与 `tools/update_apply.py`）绑定了顶层模块导入路径（如 `import chorus_project`）。为避免破坏 2.x 存量客户端增量更新与产生大规模合并冲突，当前阶段保持 `src/` 扁平布局，通过模块命名前缀（`chorus_*` / `mixdown_*` / `tuning_*` / `quick_*` / `vwa_*`）与 **Tarjan 0 循环依赖护栏** 维持清晰分层。

---

## 2. 10 分钟搭建本地开发环境

GitHub 源码仓库不包含 GB 级的 Python 便携运行库、Qt 6.10.3、FFmpeg 与模型权重。最快搭建方式是复用一份已解压的 VWA 便携版运行库：

1. **克隆仓库并切到当前活跃开发分支**：
   ```powershell
   git clone https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant.git
   cd Better-Vocaloid-Workflow-Assistant
   git checkout dev/chorus-rebuild-20261123
   git checkout -b feat/your-feature-name
   ```
2. **准备运行库**（二选一）：
   - **推荐**：下载任意一份官方便携版（Full 或 JPN），将其中的 `dependencies/`（含 `vocal2midi/python/`、`ffmpeg/`、`models/`）和 `runtime/qt6/` 复用或复制到仓库根目录（均已在 `.gitignore` 忽略，不会被提交）。
   - **仅做纯 Python / 逻辑 / L0 单测开发**：只需本机任意 Python 3.12（可选安装 `pytest` 和 `ruff`）即可直接运行所有 L0 测试与架构护栏，无需模型权重或 Qt6！
3. **生成开发副本并校验启动**：
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Prepare-Source.ps1
   & '.\dependencies\vocal2midi\python\python.exe' -B .\tools\verify_source_layout.py
   & '.\dependencies\vocal2midi\python\python.exe' -B .\launch.py --check
   ```
4. **启动桌面开发版**：
   ```powershell
   & '.\dependencies\vocal2midi\python\python.exe' -B .\launch.py
   ```

---

## 3. 原生音频 DLL 编译（CMake + CTest）

如果你修改了 `src/native/`（`vwa_native_output.c`、`vwa_limiter.c`、`vwa_chorus_pitch.cpp`）：

```powershell
# 使用 MinGW-w64（或改用 --preset msvc-release）
cmake -S src/native -B build/native -G "MinGW Makefiles" -DCMAKE_BUILD_TYPE=Release
cmake --build build/native --parallel
ctest --test-dir build/native --output-on-failure
```

- 离线构建可通过 `-DVWA_MINIAUDIO_DIR=...`、`-DVWA_SIGNALSMITH_STRETCH_DIR=...`、`-DVWA_SIGNALSMITH_LINEAR_DIR=...` 指定本地头文件目录；未指定时 CMake `FetchContent` 会按固定 commit 与 SHA-256 自动拉取。
- 编译产物（`vwa_native_output.dll`、`vwa_limiter.dll`、`vwa_chorus_pitch.dll`）复制到本地 `components/native-audio/` 即可被开发态直接加载（`.dll` 已在 `.gitignore` 中忽略）。

---

## 4. 测试分层与提 PR 前自检

VWA 将自动化与实机验收明确区分为 **四维证据**，PR 说明中需分列报告：

| 维度 | 说明 | 运行方式 |
| :--- | :--- | :--- |
| **`unit_isolated`** | 隔离单测、架构护栏、i18n 审计、L0 回归 | `python -B tests/test_architecture_guards.py` 或 `tests/run_tests.py` |
| **`real_material_dsp`** | 真实音频/GPU 推理实测（如 MDX/RoFormer 分离、修音黄金回归） | 对应 DSP/GPU 实机脚本与素材指标 |
| **`quick_offscreen`** | Qt 6 离屏界面测试（全 11 页渲染、WCAG 对比度、无溢出/无警告） | `BVWA_TEST_SELECTION="test_quick_desktop.py,test_user_experience_guards.py" python -B tests/run_tests.py` |
| **`human_listening`** | 真人整曲试听与桌面交互实测 | 人工启动桌面版实测 |

### 提 PR 前必跑自检命令（与 GitHub Actions CI 完全一致）

```powershell
# 1. 刷新生成副本并核验三向清单一致
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Prepare-Source.ps1
python -B tools/verify_source_layout.py

# 2. Ruff 关键语法/未定义名称检查
ruff check src tools tests

# 3. 国际化 100% 覆盖检查（en / ja 缺失数必须为 0）
python -B tools/extract_i18n.py --check --max-missing-en 0 --max-missing-ja 0

# 4. 架构护栏与核心 L0 回归
$env:VWA_TESTING = "1"; $env:BVWA_TESTING = "1"
python -B tests/test_source_layout.py
python -B tests/test_architecture_guards.py
python -B tests/test_tier1_infra.py
python -B tests/test_tier2_infra.py
python -B tests/test_tier3_ux.py
```

---

## 5. 五大自动化 CI 护栏（红线速查）

修改代码时请留意 `tests/test_architecture_guards.py`、`tests/test_source_layout.py` 与 `tests/test_tier1_infra.py` 锁定的 5 条硬护栏：

1. **三向清单一一对齐**：凡在 `src/` 下新增 `.py` 或 `.qml` 文件，必须同时登记到 `packaging/source-files.json` 和 `packaging/portable-files.json`；新增 `docs/*.md` 也需登记到 `packaging/source-files.json`。
2. **零循环依赖（Tarjan `SCC == 0`）**：`src/*.py` 全图禁止任何直接或跨多跳循环导入；领域/DSP 模块严禁导入 `PySide6`。
3. **文件行数棘轮**：所有**新增** `src/*.py` / `src/quick_ui/*.qml` 上限 **800 行**；已有超 800 行历史文件只允许缩小或持平，不得突破 `LEGACY_FILE_LINE_CEILINGS`。新增逻辑请优先拆为独立纯 Python 辅助模块。
4. **多语言 0 缺失**：在 `.py` / `.qml` 中新增任何中文界面文案（`tr(...)` / `vwaTr(...)` / `controller.text(...)`），必须在 `assets/i18n/additions.json` 中同步补齐 `en` 和 `ja` 译文。
5. **禁止静默吞异常**：禁止裸 `except:` 或 `except Exception: pass`，请捕获具体异常或记录日志。
