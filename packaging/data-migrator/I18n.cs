using System;
using System.Collections.Generic;
using System.Globalization;

namespace VwaDataMigrator
{
    internal static class I18n
    {
        public static string CurrentLanguage = DetectDefaultLanguage();

        private static readonly Dictionary<string, Dictionary<string, string>> Table = BuildTable();

        private static string DetectDefaultLanguage()
        {
            string name = CultureInfo.CurrentUICulture.Name ?? "";
            if (name.StartsWith("zh", StringComparison.OrdinalIgnoreCase)) return "zh-CN";
            if (name.StartsWith("ja", StringComparison.OrdinalIgnoreCase)) return "ja";
            return "en";
        }

        public static string T(string key)
        {
            Dictionary<string, string> langMap;
            string val;
            if (Table.TryGetValue(CurrentLanguage, out langMap) && langMap.TryGetValue(key, out val))
                return val;
            if (Table["zh-CN"].TryGetValue(key, out val))
                return val;
            return key;
        }

        public static string F(string key, params object[] args)
        {
            return string.Format(CultureInfo.InvariantCulture, T(key), args);
        }

        private static Dictionary<string, Dictionary<string, string>> BuildTable()
        {
            var zh = new Dictionary<string, string>
            {
                { "window_title", "VWA 数据迁移工具" },
                { "app_name", "VWA · 跨版本数据迁移助手" },
                { "subtitle", "适用 123 网盘 / 绿色便携包手动换新 · 任意旧版路径一键无损迁入新版" },
                { "badge_tool", "零依赖便携工具" },
                { "step_paths", "1. 选择旧版与新版目录" },
                { "old_dir", "旧版 VWA 目录（迁移来源）" },
                { "new_dir", "新版 VWA 目录（迁移目标）" },
                { "old_placeholder", "请选择原来的 VWA / BVWA 文件夹（含 config.json 或 jobs）" },
                { "new_placeholder", "请选择新解压的 VWA 3.0.0 文件夹（含 VWA.exe）" },
                { "browse", "浏览…" },
                { "swap_dirs", "交换目录" },
                { "dir_empty", "尚未选择目录" },
                { "dir_not_found", "目录不存在" },
                { "dir_detected", "已识别 VWA v{0}（{1}）" },
                { "dir_legacy_data", "检测到用户数据目录（未见 version.json）" },
                { "dir_no_vwa", "未检测到 VWA 特征文件，请确认路径正确" },
                { "free_space", "目标盘可用 {0}" },
                { "step_items", "2. 选择要迁移的数据内容" },
                { "item_config", "个人设置、主题与最近记录（智能合并 config.json 并自动改写路径）" },
                { "item_jobs", "任务、工程与音频结果（jobs/）" },
                { "item_presets", "本机预设库（user-presets/）" },
                { "item_accounts", "账号登录会话（cache/accounts/）" },
                { "item_publish", "投稿资料包（publish-packages/）" },
                { "item_optional", "已下载的可选模型与组件（BS-RoFormer / WebGPU ONNX / Pedalboard）" },
                { "item_qwen", "中文 / 英文 Qwen3-ASR 权重（约 4.38 GiB，目标已有则自动跳过）" },
                { "meta_none", "旧目录无此项" },
                { "meta_already_in_target", "目标已内置，无需迁移" },
                { "meta_count_size", "{0} 个文件 · {1}" },
                { "meta_config_ready", "保留新版核心路径，自动备份原配置" },
                { "step_mode", "3. 传输方式" },
                { "mode_copy", "安全复制（推荐：旧目录完整保留，随时可回退）" },
                { "mode_move", "同盘快速移动（节省空间：仅移动勾选数据，不删除旧版程序）" },
                { "status_ready", "选择旧版与新版目录后，点击「开始迁移数据」。请先关闭正在运行的 VWA。" },
                { "status_scanning", "正在扫描目录与可迁移数据…" },
                { "status_migrating_cfg", "正在智能合并个人设置与界面状态…" },
                { "status_copying", "正在迁移 ({0}/{1}) {2}…" },
                { "status_done", "迁移完成！已处理 {0} 个文件（{1}），跳过已存在 {2} 个。原配置已自动安全保留。" },
                { "status_cancelled", "已取消迁移。已完成的文件保留在目标目录，可随时再次执行（自动跳过已存在文件）。" },
                { "status_error", "迁移未完成：{0}" },
                { "btn_start", "开始迁移数据" },
                { "btn_retry", "重新迁移" },
                { "btn_cancel", "取消" },
                { "btn_cancelling", "正在取消…" },
                { "btn_launch", "立即启动新版 VWA" },
                { "btn_open_dir", "打开新版目录" },
                { "btn_rescan", "重新扫描" },
                { "footer_info", "开源免费 (MIT) · 绝不覆盖新版已有文件 · QQ群 490619616" },
                { "err_select_both", "请先同时选择有效的旧版目录与新版目录。" },
                { "err_same_dir", "旧版目录与新版目录不能相同。" },
                { "err_nested_dir", "旧版目录与新版目录不能互相包含，请分别选择独立目录。" },
                { "err_source_missing", "旧版目录不存在，请重新选择。" },
                { "err_target_missing", "新版目录不存在，请先解压新版便携包后再选择该目录。" },
                { "err_nothing_selected", "请至少勾选一项存在可迁移数据的内容。" },
                { "err_disk_space", "目标磁盘剩余空间不足：当前可用 {0}，安全复制至少需要 {1}（可改选「同盘快速移动」或清理空间）。" },
                { "err_vwa_running", "检测到目标或旧版目录中的 VWA 正在运行（存在活动锁文件），请先完全关闭 VWA 后重试。" }
            };

            var en = new Dictionary<string, string>
            {
                { "window_title", "VWA Data Migrator" },
                { "app_name", "VWA · Cross-Version Data Migrator" },
                { "subtitle", "For manual ZIP / cloud-drive downloads · Migrate any older VWA folder cleanly into your new one" },
                { "badge_tool", "Zero-Dependency Tool" },
                { "step_paths", "1. Choose Old & New VWA Folders" },
                { "old_dir", "Old VWA Folder (Source)" },
                { "new_dir", "New VWA Folder (Target)" },
                { "old_placeholder", "Select your previous VWA / BVWA folder (with config.json or jobs)" },
                { "new_placeholder", "Select your newly extracted VWA 3.0.0 folder (with VWA.exe)" },
                { "browse", "Browse…" },
                { "swap_dirs", "Swap" },
                { "dir_empty", "Not selected" },
                { "dir_not_found", "Folder does not exist" },
                { "dir_detected", "Detected VWA v{0} ({1})" },
                { "dir_legacy_data", "User data detected (no version.json)" },
                { "dir_no_vwa", "No VWA files found here — please verify the path" },
                { "free_space", "{0} free on target drive" },
                { "step_items", "2. Select Data to Migrate" },
                { "item_config", "Settings, theme & recent items (smart-merges config.json & rewrites paths)" },
                { "item_jobs", "Jobs, projects & audio results (jobs/)" },
                { "item_presets", "Local preset library (user-presets/)" },
                { "item_accounts", "Account sign-in sessions (cache/accounts/)" },
                { "item_publish", "Publishing packages (publish-packages/)" },
                { "item_optional", "Optional models & components (BS-RoFormer / WebGPU ONNX / Pedalboard)" },
                { "item_qwen", "Chinese / English Qwen3-ASR weights (~4.38 GiB, skipped if already in target)" },
                { "meta_none", "Not present in source" },
                { "meta_already_in_target", "Already included in target" },
                { "meta_count_size", "{0} files · {1}" },
                { "meta_config_ready", "Preserves new runtime paths & backs up target config" },
                { "step_mode", "3. Transfer Mode" },
                { "mode_copy", "Safe Copy (Recommended: keeps old folder intact for rollback)" },
                { "mode_move", "Fast Same-Drive Move (Saves disk space; moves selected data only)" },
                { "status_ready", "Select old and new VWA folders, close any running VWA, then click Start Migration." },
                { "status_scanning", "Scanning folders and migratable data…" },
                { "status_migrating_cfg", "Smart-merging settings and UI preferences…" },
                { "status_copying", "Migrating ({0}/{1}) {2}…" },
                { "status_done", "Migration complete! Transferred {0} files ({1}), skipped {2} existing files." },
                { "status_cancelled", "Cancelled. Already migrated files remain intact; re-run anytime to continue." },
                { "status_error", "Migration failed: {0}" },
                { "btn_start", "Start Migration" },
                { "btn_retry", "Retry Migration" },
                { "btn_cancel", "Cancel" },
                { "btn_cancelling", "Cancelling…" },
                { "btn_launch", "Launch New VWA" },
                { "btn_open_dir", "Open New Folder" },
                { "btn_rescan", "Rescan" },
                { "footer_info", "Open Source (MIT) · Never overwrites newer files · QQ 490619616" },
                { "err_select_both", "Please select both an existing old VWA folder and the new VWA folder." },
                { "err_same_dir", "Source and target folders cannot be the same." },
                { "err_nested_dir", "Source and target folders cannot be nested inside each other." },
                { "err_source_missing", "The old VWA folder does not exist." },
                { "err_target_missing", "The new VWA folder does not exist. Extract the new package first." },
                { "err_nothing_selected", "Please check at least one category that has data to migrate." },
                { "err_disk_space", "Not enough free space on target drive: {0} free, at least {1} required (try Same-Drive Move)." },
                { "err_vwa_running", "VWA appears to be running in the source or target folder. Close VWA completely and retry." }
            };

            var ja = new Dictionary<string, string>
            {
                { "window_title", "VWA データ移行ツール" },
                { "app_name", "VWA · バージョン間データ移行アシスタント" },
                { "subtitle", "手動展開・クラウド経由ダウンロード向け · 旧版フォルダーから新版へ安全にデータ移行" },
                { "badge_tool", "依存なし単体ツール" },
                { "step_paths", "1. 旧版と新版のフォルダーを選択" },
                { "old_dir", "旧版 VWA フォルダー（移行元）" },
                { "new_dir", "新版 VWA フォルダー（移行先）" },
                { "old_placeholder", "元の VWA / BVWA フォルダーを選択してください" },
                { "new_placeholder", "展開した新版 VWA 3.0.0 フォルダー（VWA.exe を含む）を選択してください" },
                { "browse", "参照…" },
                { "swap_dirs", "入れ替え" },
                { "dir_empty", "未選択" },
                { "dir_not_found", "フォルダーが存在しません" },
                { "dir_detected", "VWA v{0}（{1}）を検出" },
                { "dir_legacy_data", "ユーザーデータを検出（version.json なし）" },
                { "dir_no_vwa", "VWA ファイルが見つかりません。パスを確認してください" },
                { "free_space", "移行先ドライブ空き {0}" },
                { "step_items", "2. 移行する項目を選択" },
                { "item_config", "個人設定・テーマ・履歴（config.json を安全マージ＆パス自動書き換え）" },
                { "item_jobs", "タスク・プロジェクト・音声出力（jobs/）" },
                { "item_presets", "ローカルプリセット（user-presets/）" },
                { "item_accounts", "アカウントログイン情報（cache/accounts/）" },
                { "item_publish", "投稿用パッケージ（publish-packages/）" },
                { "item_optional", "追加モデル・コンポーネント（BS-RoFormer / WebGPU ONNX / Pedalboard）" },
                { "item_qwen", "中国語／英語 Qwen3-ASR 重み（約 4.38 GiB・移行先に同梱済みなら自動スキップ）" },
                { "meta_none", "移行元にデータなし" },
                { "meta_already_in_target", "移行先に同梱済み" },
                { "meta_count_size", "{0} 件 · {1}" },
                { "meta_config_ready", "新版のランタイム設定を保持し元設定をバックアップ" },
                { "step_mode", "3. 転送方式" },
                { "mode_copy", "安全コピー（おすすめ：旧フォルダーをそのまま残します）" },
                { "mode_move", "同一ドライブ高速移動（容量節約：選択データのみ移動）" },
                { "status_ready", "移行元と移行先を選び、起動中の VWA を終了してから「移行を開始」を押してください。" },
                { "status_scanning", "フォルダーと移行対象データをスキャン中…" },
                { "status_migrating_cfg", "設定と表示状態をマージ中…" },
                { "status_copying", "移行中 ({0}/{1}) {2}…" },
                { "status_done", "移行完了！{0} 件（{1}）を移行し、既存 {2} 件をスキップしました。" },
                { "status_cancelled", "キャンセルしました。再実行すると未移行ファイルから続けられます。" },
                { "status_error", "移行失敗：{0}" },
                { "btn_start", "移行を開始" },
                { "btn_retry", "再試行" },
                { "btn_cancel", "キャンセル" },
                { "btn_cancelling", "キャンセル中…" },
                { "btn_launch", "新版 VWA を起動" },
                { "btn_open_dir", "新版フォルダーを開く" },
                { "btn_rescan", "再スキャン" },
                { "footer_info", "オープンソース (MIT) · 既存の新版ファイルは上書きしません · QQ 490619616" },
                { "err_select_both", "移行元と移行先の両方のフォルダーを指定してください。" },
                { "err_same_dir", "移行元と移行先に同じフォルダーは指定できません。" },
                { "err_nested_dir", "移行元と移行先が入れ子になっています。別のフォルダーを指定してください。" },
                { "err_source_missing", "移行元フォルダーが見つかりません。" },
                { "err_target_missing", "移行先フォルダーが見つかりません。先に新版を展開してください。" },
                { "err_nothing_selected", "移行対象データが存在する項目を 1 つ以上選択してください。" },
                { "err_disk_space", "移行先ドライブの空き容量不足：空き {0}、必要 {1} 以上（同一ドライブ移動もお試しください）。" },
                { "err_vwa_running", "VWA が起動中です。すべての VWA ウィンドウを閉じてから再試行してください。" }
            };

            return new Dictionary<string, Dictionary<string, string>>
            {
                { "zh-CN", zh },
                { "en", en },
                { "ja", ja }
            };
        }
    }
}
