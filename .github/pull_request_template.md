<!-- 中文、English、日本語均可。一个 PR 解决一个明确目标；不适用的部分写 N/A。
Use a focused title, e.g. fix(mixdown): preserve playback after seeking.
See CONTRIBUTING.md / CONTRIBUTING.zh-CN.md. Remove these instructions when ready. -->

## 问题与结果 / Problem and result

<!-- 具体触发条件、原有表现和修改后的表现。Explain what triggers the problem and the resulting behavior. -->

## 主要改动 / Changes

<!-- 只说明影响审阅的设计与取舍，避免逐文件复述。Explain the design and relevant tradeoffs. -->

## 关联 Issue / Related issue

<!-- 仅在确实解决问题时用 Closes #123；否则用 Refs #123。小修复可以没有预先建立的 Issue。
Use Closes only when this resolves the issue; otherwise use Refs. A prior issue is not mandatory for small fixes. -->

## 验证 / Validation

<!-- 写明命令或实际操作、结果，以及未运行/跳过的检查及原因。
界面修改附相关尺寸/缩放下截图；试听或音频修改说明素材、设备及测量条件。
Give commands/manual steps, results and untested limits. Documentation-only changes need link/content checks, not application regression.
Automated/offscreen checks do not establish real-device behavior or human listening quality. -->

## 影响与迁移 / Impact and migration

<!-- 是否影响工程、预设、设置、依赖、公开接口或数据格式？破坏性变更说明迁移/恢复方法；无则写无/N/A。
Describe project/preset/settings/API/dependency changes and migration or recovery if applicable. -->

## 自查 / Checklist

- [ ] 改动围绕同一目标，无无关整理。 / The changes have one clear purpose.
- [ ] 应用代码在 src/、构建配置在 packaging/，已按需准备和登记清单；未提交生成副本、模型、账号或用户数据。 / Source/layout rules followed; generated files and private payloads excluded.
- [ ] 已如实记录验证、失败和未验证范围。 / Validation and its limits are reported accurately.
- [ ] 已处理适用的文档、界面翻译及公开接口说明；不适用部分已注明。 / Applicable docs, translations and interface notes are updated.
