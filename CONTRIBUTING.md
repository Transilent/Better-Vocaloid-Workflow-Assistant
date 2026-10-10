# Contributing

Bug reports should include the application version, Windows version, selected processing options and the relevant error text. Share a short reproducible input when possible. Remove cookies, account data and private media from logs before uploading them.

## Development

Install the portable runtime using `tools/Start-Diagnostics.bat`. Build the GUI launcher with `tools/build_windows_launcher.py`, then run `tests/run_tests.py` using the bundled Python. Inference weights and browser binaries are excluded from Git.

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

提交问题时请附上版本、系统、处理选项和相关报错，并清除日志中的账号及私人信息。文档以英文为主，同时维护中文版本。面向用户的文字应直接说明功能、操作与恢复方法。

正式打包及发布前，先完成自动检查和界面检查，再提供本地版本给用户测试。收到明确确认后才能使用 `--user-test-confirmed` 构建发行包；此参数不能代替用户确认。
