# VWA VERSION

A short description of the main benefits for users.

## What’s new

- Describe the available functions and where to find them.

## Choose a package

- **Full package:** `VWA-Windows-x64.zip` for a new installation with all included lyric models. Its split metadata is `portable-metadata-full.json`.
- **JPN package:** `VWA-Windows-x64-JPN.zip` for Japanese lyrics and note extraction without the Chinese Qwen weights. Its split metadata is `portable-metadata-jpn.json`. Add Chinese lyric recognition later from Models.
- **Update package:** use the compatible update for an existing installation. Settings, tasks and installed models are retained.

## Start and update

Extract the archive. Its top-level folder is `VWA`; keep the contents together and start **`VWA.exe`**. If the download is split, keep all parts with the matching `portable-metadata-full.json` or `portable-metadata-jpn.json` and use the supplied merge helper. Source code archives and update archives are not full installations.

For an existing installation, use **Settings and updates** and restart to install. Released clients that still use legacy BVWA protocol aliases should follow the release notes and run **`VWA-Updater.exe`** when it is provided as the compatibility tool. Select the interface language under **Appearance**. Install optional models from **Models**.

## Known limitations

List only limitations that affect users, with a practical workaround.

## 中文说明

简述新增功能与操作入口。全量包为 `VWA-Windows-x64.zip`，其分卷清单为 `portable-metadata-full.json`；JPN 包为 `VWA-Windows-x64-JPN.zip`，其分卷清单为 `portable-metadata-jpn.json`。JPN 包不含中文 Qwen 权重，可稍后在模型与组件中下载。解压后的顶层文件夹为 `VWA`，启动 **`VWA.exe`**，界面语言在外观设置中选择。

旧版 BVWA 客户端需要兼容路径时，按发行说明运行 **`VWA-Updater.exe`**。更新包不能作为完整安装包使用。
