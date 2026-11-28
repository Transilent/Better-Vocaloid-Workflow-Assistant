using System;
using System.Collections.Generic;
using System.Globalization;

namespace VwaOnlineInstaller
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
                { "window_title", "VWA 在线引导安装器" },
                { "app_name", "VWA · 术力口工作流助手" },
                { "subtitle", "官方单文件在线安装器 · 自动镜像竞速、多线程断点续传与 SHA-256 校验" },
                { "checking_release", "正在获取最新版本信息…" },
                { "release_ready", "最新版本 v{0}" },
                { "release_pinned", "指定版本 v{0}" },
                { "release_fail", "获取版本信息失败，可切换下载线路后重试" },
                { "step_choose", "选择安装版本" },
                { "full_title", "Full 完整版" },
                { "full_badge", "推荐 · 开箱即用" },
                { "full_desc", "包含全部人声分离、日语/英语/中文 Qwen 歌词识别与完整工作台。自动多线程分卷下载并校验，免手动合并。" },
                { "jpn_title", "JPN 精简版" },
                { "jpn_badge", "轻量快速" },
                { "jpn_desc", "体积更小，包含全部调音、合唱、混音、视频与日语/英语识别；中文 Qwen 模型可安装后在「模型与组件」页按需补装。" },
                { "size_loading", "读取大小中…" },
                { "size_parts", "{0} · {1} 个分卷" },
                { "size_single", "{0} · 单文件" },
                { "install_dir", "安装目录" },
                { "browse", "浏览…" },
                { "free_space", "可用 {0}" },
                { "upgrade_detected", "检测到已有 VWA v{0}，将无损保留配置、工程、预设与已下载模型" },
                { "network_route", "下载线路" },
                { "route_auto", "自动竞速（推荐）" },
                { "route_mirror", "优先国内加速源" },
                { "route_direct", "GitHub 官方直连" },
                { "route_gitproxy", "GitProxy 加速" },
                { "route_ghproxy", "GH-Proxy 加速" },
                { "route_ghfast", "ghfast.top 加速" },
                { "proxy_mode", "网络代理" },
                { "proxy_system", "跟随系统代理" },
                { "proxy_direct", "不使用代理（直连）" },
                { "proxy_custom", "自定义代理" },
                { "proxy_placeholder", "例如 http://127.0.0.1:7890" },
                { "opt_startmenu", "创建开始菜单快捷方式" },
                { "opt_desktop", "创建桌面快捷方式" },
                { "opt_launch", "安装完成后立即启动 VWA" },
                { "stage_download", "1. 并发下载" },
                { "stage_verify", "2. 校验合并" },
                { "stage_extract", "3. 安全解压" },
                { "stage_finish", "4. 完成配置" },
                { "status_ready", "准备就绪。选择版本与安装目录后点击「开始下载并安装」。" },
                { "status_probing", "正在竞速探测最快下载线路…" },
                { "status_downloading", "正在下载分卷 ({0}/{1}) {2}…" },
                { "status_merging", "正在合并 {0} 个分卷并核验完整包 SHA-256…" },
                { "status_verifying", "正在核验文件完整性（SHA-256）…" },
                { "status_extracting", "正在安全解压安装文件 ({0}/{1})…" },
                { "status_migrating", "正在保留现有用户数据并写入初始配置…" },
                { "status_shortcuts", "正在创建快捷方式与卸载信息…" },
                { "status_done", "安装完成！VWA v{0} 已准备就绪。" },
                { "status_cancelled", "已取消安装；已下载分片已保存在缓存中，下次点击可断点续传。" },
                { "status_error", "安装未完成：{0}" },
                { "btn_install", "开始下载并安装" },
                { "btn_retry", "重试（断点续传）" },
                { "btn_cancel", "取消" },
                { "btn_cancelling", "正在取消…" },
                { "btn_launch", "立即启动 VWA" },
                { "btn_open_dir", "打开安装目录" },
                { "btn_refresh", "刷新版本" },
                { "eta_fmt", "剩余 {0}" },
                { "route_pill", "线路：{0} · {1} 线程" },
                { "footer_info", "开源免费 (MIT) · 交流群 QQ 490619616" },
                { "footer_releases", "GitHub 发布页" },
                { "err_disk_space", "目标磁盘剩余空间不足：当前可用 {0}，安装至少需要 {1}。" },
                { "err_invalid_dir", "请选择有效的可写安装目录。" },
                { "err_invalid_proxy", "自定义代理地址格式无效，请填写 http:// 或 https:// 开头的地址。" },
                { "err_no_release", "尚未加载到版本元数据，请先点击「刷新版本」或切换网络线路。" }
            };

            var en = new Dictionary<string, string>
            {
                { "window_title", "VWA Online Installer" },
                { "app_name", "VWA · Vocaloid Workflow Assistant" },
                { "subtitle", "Official single-file web installer · Mirror racing, multi-threaded resume & SHA-256 verification" },
                { "checking_release", "Checking latest release…" },
                { "release_ready", "Latest v{0}" },
                { "release_pinned", "Version v{0}" },
                { "release_fail", "Failed to load release info. Switch route and retry." },
                { "step_choose", "Choose Edition" },
                { "full_title", "Full Edition" },
                { "full_badge", "Recommended" },
                { "full_desc", "Includes all separation, Japanese/English/Chinese Qwen ASR models & full studio. Auto split-part download & verify." },
                { "jpn_title", "JPN Compact Edition" },
                { "jpn_badge", "Fast & Light" },
                { "jpn_desc", "Smaller download with full tuning, chorus, mixdown, video & JP/EN ASR. Chinese Qwen model can be added later in Components." },
                { "size_loading", "Checking size…" },
                { "size_parts", "{0} · {1} parts" },
                { "size_single", "{0} · Single ZIP" },
                { "install_dir", "Install Folder" },
                { "browse", "Browse…" },
                { "free_space", "{0} free" },
                { "upgrade_detected", "Existing VWA v{0} detected — config, jobs, presets & optional models will be preserved" },
                { "network_route", "Download Route" },
                { "route_auto", "Auto Race (Recommended)" },
                { "route_mirror", "Prefer Mirrors" },
                { "route_direct", "GitHub Direct" },
                { "route_gitproxy", "GitProxy Mirror" },
                { "route_ghproxy", "GH-Proxy Mirror" },
                { "route_ghfast", "ghfast.top Mirror" },
                { "proxy_mode", "Network Proxy" },
                { "proxy_system", "System Proxy" },
                { "proxy_direct", "Direct (No Proxy)" },
                { "proxy_custom", "Custom Proxy" },
                { "proxy_placeholder", "e.g. http://127.0.0.1:7890" },
                { "opt_startmenu", "Create Start Menu shortcuts" },
                { "opt_desktop", "Create Desktop shortcut" },
                { "opt_launch", "Launch VWA immediately after install" },
                { "stage_download", "1. Download" },
                { "stage_verify", "2. Verify & Merge" },
                { "stage_extract", "3. Extract" },
                { "stage_finish", "4. Finish" },
                { "status_ready", "Ready. Choose an edition and install folder, then click Download & Install." },
                { "status_probing", "Probing fastest download mirror…" },
                { "status_downloading", "Downloading part ({0}/{1}) {2}…" },
                { "status_merging", "Merging {0} parts & verifying full archive SHA-256…" },
                { "status_verifying", "Verifying SHA-256 integrity…" },
                { "status_extracting", "Extracting verified files ({0}/{1})…" },
                { "status_migrating", "Preserving user data & writing initial config…" },
                { "status_shortcuts", "Creating shortcuts & uninstall registration…" },
                { "status_done", "Installation complete! VWA v{0} is ready." },
                { "status_cancelled", "Cancelled. Downloaded chunks are cached and will resume next time." },
                { "status_error", "Install failed: {0}" },
                { "btn_install", "Download & Install" },
                { "btn_retry", "Retry (Resume)" },
                { "btn_cancel", "Cancel" },
                { "btn_cancelling", "Cancelling…" },
                { "btn_launch", "Launch VWA" },
                { "btn_open_dir", "Open Folder" },
                { "btn_refresh", "Refresh" },
                { "eta_fmt", "ETA {0}" },
                { "route_pill", "Route: {0} · {1} threads" },
                { "footer_info", "Open Source (MIT) · Community QQ 490619616" },
                { "footer_releases", "GitHub Releases" },
                { "err_disk_space", "Insufficient disk space: {0} free, at least {1} required." },
                { "err_invalid_dir", "Please choose a valid writable install folder." },
                { "err_invalid_proxy", "Invalid proxy URL. Use an http:// or https:// address." },
                { "err_no_release", "Release metadata is not loaded yet. Click Refresh or switch route." }
            };

            var ja = new Dictionary<string, string>
            {
                { "window_title", "VWA オンラインインストーラー" },
                { "app_name", "VWA · Vocaloid Workflow Assistant" },
                { "subtitle", "公式オンラインインストーラー · 最速ミラー自動選択・マルチスレッド再開・SHA-256 検証" },
                { "checking_release", "最新リリース情報を取得中…" },
                { "release_ready", "最新版 v{0}" },
                { "release_pinned", "指定版 v{0}" },
                { "release_fail", "リリース情報の取得に失敗しました。回線を切り替えて再試行してください。" },
                { "step_choose", "エディション選択" },
                { "full_title", "Full 完全版" },
                { "full_badge", "おすすめ" },
                { "full_desc", "音源分離、日本語・英語・中国語 Qwen 歌詞認識と全ワークベンチを同梱。分割アーカイブを自動ダウンロード・検証します。" },
                { "jpn_title", "JPN 軽量版" },
                { "jpn_badge", "高速・軽量" },
                { "jpn_desc", "調声・コーラス・ミックス・動画合成と日本語／英語認識を同梱。中国語モデルは後からコンポーネント画面で追加できます。" },
                { "size_loading", "サイズ取得中…" },
                { "size_parts", "{0} · {1} 分割" },
                { "size_single", "{0} · 単一 ZIP" },
                { "install_dir", "インストール先" },
                { "browse", "参照…" },
                { "free_space", "空き {0}" },
                { "upgrade_detected", "既存の VWA v{0} を検出 — 設定・ジョブ・プリセット・モデルは保持されます" },
                { "network_route", "ダウンロード回線" },
                { "route_auto", "自動最速（おすすめ）" },
                { "route_mirror", "ミラー優先" },
                { "route_direct", "GitHub 公式直結" },
                { "route_gitproxy", "GitProxy" },
                { "route_ghproxy", "GH-Proxy" },
                { "route_ghfast", "ghfast.top" },
                { "proxy_mode", "プロキシ設定" },
                { "proxy_system", "システムプロキシ" },
                { "proxy_direct", "プロキシなし（直結）" },
                { "proxy_custom", "カスタムプロキシ" },
                { "proxy_placeholder", "例: http://127.0.0.1:7890" },
                { "opt_startmenu", "スタートメニューにショートカットを作成" },
                { "opt_desktop", "デスクトップにショートカットを作成" },
                { "opt_launch", "インストール完了後に VWA を起動" },
                { "stage_download", "1. ダウンロード" },
                { "stage_verify", "2. 検証・結合" },
                { "stage_extract", "3. 展開" },
                { "stage_finish", "4. 完了" },
                { "status_ready", "準備完了。エディションと保存先を選んで「ダウンロードしてインストール」を押してください。" },
                { "status_probing", "最速のダウンロード回線を計測中…" },
                { "status_downloading", "パート ({0}/{1}) {2} をダウンロード中…" },
                { "status_merging", "{0} パートを結合し SHA-256 を検証中…" },
                { "status_verifying", "SHA-256 整合性を検証中…" },
                { "status_extracting", "ファイルを安全に展開中 ({0}/{1})…" },
                { "status_migrating", "既存データを保持し初期設定を書き込み中…" },
                { "status_shortcuts", "ショートカットとアンインストール情報を登録中…" },
                { "status_done", "インストール完了！VWA v{0} の準備が整いました。" },
                { "status_cancelled", "キャンセルしました。ダウンロード済みチャンクは保持され、次回再開できます。" },
                { "status_error", "インストール失敗：{0}" },
                { "btn_install", "ダウンロードしてインストール" },
                { "btn_retry", "再試行（続きから再開）" },
                { "btn_cancel", "キャンセル" },
                { "btn_cancelling", "キャンセル中…" },
                { "btn_launch", "VWA を起動" },
                { "btn_open_dir", "フォルダを開く" },
                { "btn_refresh", "更新" },
                { "eta_fmt", "残り {0}" },
                { "route_pill", "回線: {0} · {1} スレッド" },
                { "footer_info", "オープンソース (MIT) · コミュニティ QQ 490619616" },
                { "footer_releases", "GitHub リリース" },
                { "err_disk_space", "空き容量不足：現在の空き {0}、必要容量 {1} 以上。" },
                { "err_invalid_dir", "書き込み可能なインストール先フォルダを指定してください。" },
                { "err_invalid_proxy", "プロキシ URL が無効です。http:// または https:// で指定してください。" },
                { "err_no_release", "リリース情報がまだ読み込まれていません。更新を押すか回線を切り替えてください。" }
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
