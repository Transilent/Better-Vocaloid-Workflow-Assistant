using System;
using System.Globalization;
using System.Threading;
using System.Windows;

namespace VwaDataMigrator
{
    internal static class Program
    {
        [STAThread]
        public static int Main(string[] args)
        {
            string sourceDir = "";
            string targetDir = "";
            string mode = "copy";
            bool headless = false;
            bool smokeUi = false;
            bool noLaunch = false;
            string capturePng = "";
            string themeArg = "";
            var opts = new MigrationOptions();

            for (int i = 0; i < args.Length; i++)
            {
                string a = args[i];
                if (a == "--headless") headless = true;
                else if (a == "--smoke-ui") smokeUi = true;
                else if (a == "--no-launch") noLaunch = true;
                else if (a == "--source" && i + 1 < args.Length) sourceDir = args[++i];
                else if (a == "--target" && i + 1 < args.Length) targetDir = args[++i];
                else if (a == "--mode" && i + 1 < args.Length) mode = args[++i].ToLowerInvariant();
                else if (a == "--capture-png" && i + 1 < args.Length) capturePng = args[++i];
                else if (a == "--theme" && i + 1 < args.Length) themeArg = args[++i].ToLowerInvariant();
                else if (a == "--lang" && i + 1 < args.Length) I18n.CurrentLanguage = args[++i];
                else if (a == "--skip-config") opts.IncludeConfig = false;
                else if (a == "--skip-jobs") opts.IncludeJobs = false;
                else if (a == "--skip-presets") opts.IncludePresets = false;
                else if (a == "--skip-accounts") opts.IncludeAccounts = false;
                else if (a == "--skip-publish") opts.IncludePublish = false;
                else if (a == "--skip-optional") opts.IncludeOptional = false;
                else if (a == "--skip-qwen") opts.IncludeQwen = false;
            }

            if (headless)
            {
                try { Console.OutputEncoding = new System.Text.UTF8Encoding(false); } catch (Exception) { }
                opts.SourceDir = sourceDir;
                opts.TargetDir = targetDir;
                opts.MoveMode = (mode == "move");
                try
                {
                    var res = MigrationEngine.ExecuteAsync(opts, delegate (MigrationProgress p)
                    {
                        Console.WriteLine(string.Format(CultureInfo.InvariantCulture, "[{0:0.0}%] {1}", p.Percent, p.Message));
                    }, CancellationToken.None).GetAwaiter().GetResult();
                    if (!noLaunch) WindowsHelpers.LaunchVwa(targetDir);
                    Console.WriteLine(string.Format(CultureInfo.InvariantCulture,
                        "HEADLESS_MIGRATE_OK files={0} skipped={1} bytes={2} config={3} state={4} qwen={5}",
                        res.FilesTransferred, res.FilesSkipped, res.BytesTransferred,
                        res.ConfigMerged, res.StateFilesMigrated, res.QwenWeightsTransferred));
                    return 0;
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("HEADLESS_MIGRATE_ERROR: " + ex.Message);
                    return 1;
                }
            }

            var app = new Application();
            var win = new MigratorWindow(sourceDir, targetDir);
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
    }
}
