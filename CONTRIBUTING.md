# Contributing

[中文贡献规范](CONTRIBUTING.zh-CN.md) · [Feedback and support](.github/SUPPORT.md)

Chinese, English and Japanese are welcome in issues and pull requests.

## Issues

Search [existing issues](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues), then use the [issue chooser](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues/new/choose):

- **Bug report**: version/commit, installation source, Windows version, expected/actual behavior and reproduction steps/frequency. Add only relevant hardware, device and processing options. If a detail is unknown, say so.
- **Feature request**: the use case, current difficulty and desired behavior; alternatives or reference projects are optional.
- **Help**: the goal, the page/options, messages shown and what you tried.

Use one issue per problem and a concrete title: `[Bug]: mixing playback pauses after seeking`, `[Feature]: import a preset repository`, or `[Question]: how to select a vocal track`. Add new evidence to an existing matching issue and cross-link related reports. Forms add the existing `bug`, `enhancement` or `question` label; maintainers handle status and priority.

Logs and screenshots are optional. Public issues and their attachments are public. Follow the [support guide](.github/SUPPORT.md) to select and redact a short relevant excerpt; do not upload configuration, account/browser data, projects, media, models or voicebanks. Opening the feedback page does not upload anything.

## Pull requests and commits

1. Fork the repository or create a focused branch from current `main`, for example `fix/mixdown-seek` or `docs/feedback-guide`.
2. Keep one purpose per PR. Discuss large architecture, dependency, public-interface or data-format changes in an issue first; small fixes and documentation changes do not require a prior issue.
3. Target `main` and fill in the PR template: problem/result, main design choices, related issue, verification and impact/migration. Use `Refs #123` for context; use `Closes #123` only if the change resolves that issue. Open a draft when the work is incomplete.
4. Report the commands or manual steps you actually ran, results, failures and untested limits. Add screenshots for UI changes, and measurement conditions for performance/audio claims. Documentation-only changes need content/link checks rather than application regression. Offscreen tests, real devices and human listening are distinct evidence.
5. Respond to review with follow-up commits and update the PR description to match the final change. Do not include unrelated formatting, generated root copies, logs, local memory data, credentials or private assets.

Use `type(scope): concise result` for PR titles and commit subjects. The scope is optional; Chinese, English or Japanese summaries are accepted. Common types: `feat`, `fix`, `refactor`, `perf`, `docs`, `test`, `build`, `ci`, `chore`. Example: `fix(mixdown): preserve playback after seeking`. A breaking change uses `!`, for example `feat(presets)!: revise the preset schema`, and explains migration in the description. The convention guides review; it is not an automatic title gate.

## Development

Edit application code in `src/` and build metadata in `packaging/`. Register new source/distribution files in the relevant `packaging/source-files.json` and `packaging/portable-files.json` manifests; register executable regression scripts in `tests/run_tests.py`. Run `tools/Prepare-Source.ps1` after source edits to refresh the ignored development files in the checkout root. Do not hand-edit or commit those generated copies.

Use a prepared development runtime; GitHub source archives are not complete installations. `tools/Start-Diagnostics.bat` starts diagnostics and writes a log; it does not install the runtime. See the [portable guide](docs/Portable.en.md) for runtime/package details. Run `tests/run_tests.py` with the project Python and select affected registered scripts using `BVWA_TEST_SELECTION`. The runner isolates state; do not point ad hoc tests at your personal projects or account folders. Inference weights and browser binaries are excluded from Git.

For example, with the project runtime already available:

```powershell
& ./tools/Prepare-Source.ps1
$projectPython = 'C:/path/to/development-runtime/python.exe'
$previousSelection = $env:BVWA_TEST_SELECTION
try {
    $env:BVWA_TEST_SELECTION = 'test_source_layout.py'
    & $projectPython -B tests/run_tests.py
} finally {
    $env:BVWA_TEST_SELECTION = $previousSelection
}
```

Select the relevant scripts for a code change; the layout example alone does not validate feature behavior. GUI launcher changes use `tools/build_windows_launcher.py`; ordinary documentation PRs do not need a launcher build.

Keep English documentation as the main version and update its Chinese counterpart. User-facing text should describe the feature, action or recovery step directly. Implementation details belong in developer documentation unless they help a user choose an option.

## Release process

1. Run automated checks and inspect the interface at its default size and on a smaller screen.
2. Give the local build to the user for testing. Obtain explicit confirmation before creating distributable archives or a GitHub release.
3. Update the version and changelog, and build the launcher.
4. Use `tools/build_portable.py --user-test-confirmed` to create the complete package. Use `tools/build_app_update.py --user-test-confirmed` for the program update package.
5. Verify the actual archives, hashes, startup and a representative audio-to-MIDI task.
6. Upload required runtime parts, merge tool, program update and `release-assets.json`. Publish concise release notes using `.github/RELEASE_TEMPLATE.md`.

The confirmation flag records a completed review step; it does not replace user approval. Do not enable it before confirmation.

## 中文说明

完整流程见[中文贡献规范](CONTRIBUTING.zh-CN.md)。问题反馈、功能建议和使用求助均从 [Issue 表单](https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/issues/new/choose)开始。PR 面向 `main`，保持单一目标，说明行为、验证与迁移影响；标题建议使用 `fix(mixdown): 修复定位后的播放状态` 等格式。日志与附件公开可见，提交前请检查和脱敏。

正式打包及发布前，先完成自动检查和界面检查，再提供本地版本给用户测试。收到明确确认后才能使用 `--user-test-confirmed` 构建发行包；此参数不能代替用户确认。
