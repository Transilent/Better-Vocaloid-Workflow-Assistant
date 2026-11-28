using System;
using System.Globalization;
using System.Net.Http;
using System.Threading;
using System.Windows;

namespace VwaOnlineInstaller
{
    internal static class Program
    {
        [STAThread]
        public static int Main(string[] args)
        {
            string pinnedVersion = "";
            string manifestBase = "";
            string variant = "full";
            string targetDir = WindowsIntegration.DefaultInstallDir();
            string route = "auto";
            string proxyMode = "system";
            string proxyUrl = "";
            int workers = 4;
            int chunkBytes = 16 * 1024 * 1024;
            long parallelMin = 8 * 1024 * 1024;
            bool headless = false;
            bool noShortcuts = false;
            bool noLaunch = false;
            bool smokeUi = false;
            string capturePng = "";
            string themeArg = "";

            for (int i = 0; i < args.Length; i++)
            {
                string a = args[i];
                if (a == "--headless") headless = true;
                else if (a == "--no-shortcuts") noShortcuts = true;
                else if (a == "--no-launch") noLaunch = true;
                else if (a == "--smoke-ui") smokeUi = true;
                else if (a == "--capture-png" && i + 1 < args.Length) capturePng = args[++i];
                else if (a == "--theme" && i + 1 < args.Length) themeArg = args[++i].ToLowerInvariant();
                else if (a == "--version" && i + 1 < args.Length) pinnedVersion = args[++i];
                else if (a == "--manifest-base" && i + 1 < args.Length) manifestBase = args[++i];
                else if (a == "--variant" && i + 1 < args.Length) variant = args[++i].ToLowerInvariant();
                else if (a == "--target-dir" && i + 1 < args.Length) targetDir = args[++i];
                else if (a == "--route" && i + 1 < args.Length) route = args[++i].ToLowerInvariant();
                else if (a == "--proxy-mode" && i + 1 < args.Length) proxyMode = args[++i].ToLowerInvariant();
                else if (a == "--proxy-url" && i + 1 < args.Length) proxyUrl = args[++i];
                else if (a == "--workers" && i + 1 < args.Length) int.TryParse(args[++i], out workers);
                else if (a == "--chunk-bytes" && i + 1 < args.Length) int.TryParse(args[++i], out chunkBytes);
                else if (a == "--parallel-min" && i + 1 < args.Length) long.TryParse(args[++i], out parallelMin);
                else if (a == "--lang" && i + 1 < args.Length) I18n.CurrentLanguage = args[++i];
            }

            if (headless)
            {
                try { Console.OutputEncoding = new System.Text.UTF8Encoding(false); } catch (Exception) { }
                return RunHeadless(pinnedVersion, manifestBase, variant, targetDir, route, proxyMode, proxyUrl,
                    workers, chunkBytes, parallelMin, !noShortcuts, !noLaunch);
            }

            var app = new Application();
            var win = new InstallerWindow(pinnedVersion, manifestBase);
            if (themeArg == "light") win.SetThemeDark(false);
            if (smokeUi || !string.IsNullOrEmpty(capturePng))
            {
                win.ContentRendered += delegate
                {
                    if (!string.IsNullOrEmpty(capturePng)) win.SaveScreenshot(capturePng);
                    Console.WriteLine("SMOKE_UI_LOADED title=" + win.Title);
                    win.Close();
                };
            }
            app.Run(win);
            return 0;
        }

        private static int RunHeadless(
            string pinnedVersion, string manifestBase, string variant, string targetDir,
            string route, string proxyMode, string proxyUrl, int workers, int chunkBytes, long parallelMin,
            bool shortcuts, bool launch)
        {
            try
            {
                using (HttpClient client = DownloadEngine.CreateHttpClient(proxyMode, proxyUrl))
                {
                    ReleaseInfo rel = ReleaseCatalog.FetchReleaseAsync(
                        client, pinnedVersion, route, manifestBase, CancellationToken.None).GetAwaiter().GetResult();
                    PortableVariantPlan plan = (variant == "jpn") ? rel.JpnPlan : rel.FullPlan;
                    if (plan == null)
                        throw new InvalidOperationException("Variant '" + variant + "' is not available in v" + rel.Version);

                    var opts = new InstallOptions
                    {
                        Variant = variant,
                        TargetDir = targetDir,
                        RouteSource = route,
                        ProxyMode = proxyMode,
                        ProxyUrl = proxyUrl,
                        Workers = Math.Max(1, Math.Min(8, workers)),
                        ChunkBytes = chunkBytes,
                        ParallelMinBytes = parallelMin,
                        CreateStartMenu = shortcuts,
                        CreateDesktop = shortcuts,
                        LaunchAfter = launch,
                        ManifestBaseOverride = manifestBase
                    };
                    DownloadEngine.ExecuteInstallAsync(client, plan, opts, delegate (InstallProgress p)
                    {
                        Console.WriteLine(string.Format(CultureInfo.InvariantCulture,
                            "[stage={0} {1:0.0}%] {2}", p.Stage, p.Percent, p.Message));
                    }, CancellationToken.None).GetAwaiter().GetResult();

                    if (launch) WindowsIntegration.LaunchVwa(targetDir);
                    Console.WriteLine("HEADLESS_INSTALL_OK version=" + rel.Version + " variant=" + variant + " dir=" + targetDir);
                    return 0;
                }
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine("HEADLESS_INSTALL_ERROR: " + ex.Message);
                return 1;
            }
        }
    }
}
