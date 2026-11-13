# VWA 2.0.0 runtime notices / 运行库与对应源码

The Windows packages and the 2.0.0 update load separate Qt shared libraries from `runtime/qt6`. These are unmodified files selected from the public PyPI Windows x64 wheels for **PySide6 Essentials 6.10.3** and **Shiboken6 6.10.3**; see the [wheel lock](../packaging/requirements-quick.lock.txt). VWA's original source is under MIT; the third-party components retain their own terms.

Windows 包及增量更新从 `runtime/qt6` 加载独立的 Qt 库。文件选自 PyPI 的 **PySide6 Essentials 6.10.3** 和 **Shiboken6 6.10.3** Windows x64 wheel，未修改二进制，原包摘要见[依赖锁](../packaging/requirements-quick.lock.txt)。VWA 原创源码采用 MIT，第三方文件适用各自许可。

| Component / 组件 | License / 许可 | Corresponding source / 对应源码 |
| --- | --- | --- |
| PySide6 Essentials and Shiboken6 6.10.3 | LGPL-3.0 for the bindings; see [Qt for Python](https://doc.qt.io/qtforpython-6.10/). | [PySide and Shiboken 6.10.3 source](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.10.3-src/pyside-setup-everywhere-src-6.10.3.zip) · [upstream checksum](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.10.3-src/pyside-setup-everywhere-src-6.10.3.zip.mirrorlist) |
| Qt 6.10.3 libraries and QML modules | LGPL-3.0 where available; Qt Quick Timeline and other GPL-only modules, where present, use GPL-3.0. See [module licenses](https://doc.qt.io/qt-6.10/licensing.html) and [Qt third-party notices](https://doc.qt.io/qt-6.10/licenses-used-in-qt.html). | [Qt 6.10.3 source](https://download.qt.io/archive/qt/6.10/6.10.3/single/qt-everywhere-src-6.10.3.tar.xz) · [upstream checksum](https://download.qt.io/archive/qt/6.10/6.10.3/single/qt-everywhere-src-6.10.3.tar.xz.mirrorlist) |

The [LGPL v3](licenses/LGPL-3.0-only.txt) and [GPL v3](licenses/GPL-3.0-only.txt) texts are provided here and in the Release attachment `VWA-Third-Party-Notices.zip`. To replace a library with a compatible build, close VWA, back up `runtime/qt6`, and keep the same folder layout. See the [Windows build guide](https://doc.qt.io/qtforpython-6.10/building_from_source/windows.html). If a source link stops working, report it through [GitHub Issues](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues/new/choose).

[LGPL v3](licenses/LGPL-3.0-only.txt) 和 [GPL v3](licenses/GPL-3.0-only.txt) 全文亦随 Release 附件 `VWA-Third-Party-Notices.zip` 提供。可关闭 VWA、备份 `runtime/qt6`，在原目录布局中替换 ABI 兼容的库。参见[上游 Windows 构建说明](https://doc.qt.io/qtforpython-6.10/building_from_source/windows.html)；如源码链接失效，可在 [GitHub Issues](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues/new/choose) 反馈。

The [original Qwen3-ASR model](https://huggingface.co/Qwen/Qwen3-ASR-1.7B/blob/b188e100bd85038c06d2812d24a39776eba774ca/README.md) declares Apache-2.0. The [pinned conversion](https://huggingface.co/xian-taidu/Qwen3-ASR-1.7B-DML/tree/ba77390c544ce36b6736d95a1c25adcf4651566c) declares MIT in the model metadata; its pinned snapshot contains no separate license or notice file. JPN omits those weights.

[Qwen3-ASR 原始模型](https://huggingface.co/Qwen/Qwen3-ASR-1.7B/blob/b188e100bd85038c06d2812d24a39776eba774ca/README.md)声明 Apache-2.0，[固定转换版本](https://huggingface.co/xian-taidu/Qwen3-ASR-1.7B-DML/tree/ba77390c544ce36b6736d95a1c25adcf4651566c)在模型元数据中声明 MIT，固定快照内未见独立许可文件或声明。JPN 包省略这些权重。

Other components / 其他组件：[Third-party notices](../THIRD_PARTY_NOTICES.md) · [第三方说明](../THIRD_PARTY_NOTICES.zh-CN.md)。
