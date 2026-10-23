using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

[assembly: AssemblyTitle("VWA Compatibility Updater")]
[assembly: AssemblyVersion("__VERSION__")]
[assembly: AssemblyFileVersion("__VERSION__")]

internal static class UpdateBridge
{
    private static string Quote(string value)
    {
        var result = new StringBuilder("\""); int slashes = 0;
        foreach (char c in value)
        {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') { result.Append('\\', slashes * 2 + 1); result.Append(c); }
            else { result.Append('\\', slashes); result.Append(c); }
            slashes = 0;
        }
        result.Append('\\', slashes * 2); result.Append('"'); return result.ToString();
    }
    private static void CheckDirectory(string path)
    {
        if (Directory.Exists(path) && (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
            throw new IOException("更新目录包含链接，请使用普通本地目录。");
    }
    private static void Extract(string resource, string destination)
    {
        using (var source = Assembly.GetExecutingAssembly().GetManifestResourceStream(resource))
        using (var target = new FileStream(destination, FileMode.CreateNew, FileAccess.Write))
        {
            if (source == null) throw new IOException("兼容工具缺少内置文件。");
            source.CopyTo(target);
        }
    }
    [STAThread]
    private static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory; string stage = null;
        bool diagnostic = Array.Exists(args, arg => arg == "--check" || arg == "--smoke-test");
        try
        {
            for (int i = 0; i < args.Length; i++)
                if (args[i] == "--root") { if (++i >= args.Length) throw new ArgumentException("Missing --root"); root = args[i]; }
            root = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar);
            if (!File.Exists(Path.Combine(root, "dependencies", "manifest.json")))
            {
                if (diagnostic) throw new IOException("No installed assistant at the selected location.");
                using (var dialog = new FolderBrowserDialog())
                {
                    dialog.Description = "选择原助手目录（包含 dependencies 文件夹），任务和模型会保留。";
                    if (dialog.ShowDialog() != DialogResult.OK) return 0;
                    root = Path.GetFullPath(dialog.SelectedPath).TrimEnd(Path.DirectorySeparatorChar);
                }
            }
            string runtime = Path.Combine(root, "dependencies", "vocal2midi", "python");
            string python = Path.Combine(runtime, "pythonw.exe");
            if (!File.Exists(python) || !File.Exists(Path.Combine(root, "dependencies", "manifest.json")))
                throw new IOException("所选目录缺少原运行库。请选择已经安装好的助手目录。");
            string cache = Path.Combine(root, "cache"); string updates = Path.Combine(cache, "updates");
            CheckDirectory(root); CheckDirectory(cache); CheckDirectory(updates); Directory.CreateDirectory(updates);
            stage = Path.Combine(updates, "ct-" + Guid.NewGuid().ToString("N").Substring(0, 12));
            Directory.CreateDirectory(stage);
            Extract("BridgeScript", Path.Combine(stage, "update_bridge.py"));
            Extract("UpdateInstaller", Path.Combine(stage, "update_apply.py"));
            string qt = Path.Combine(runtime, "Lib", "site-packages", "PyQt5", "Qt5");
            Environment.SetEnvironmentVariable("PYTHONUTF8", "1");
            Environment.SetEnvironmentVariable("PYTHONDONTWRITEBYTECODE", "1");
            Environment.SetEnvironmentVariable("PYTHONNOUSERSITE", "1");
            Environment.SetEnvironmentVariable("VWA_LAUNCHER_PID", Process.GetCurrentProcess().Id.ToString());
            Environment.SetEnvironmentVariable("BVWA_LAUNCHER_PID", Process.GetCurrentProcess().Id.ToString());
            Environment.SetEnvironmentVariable("QT_PLUGIN_PATH", Path.Combine(qt, "plugins"));
            Environment.SetEnvironmentVariable("QT_QPA_PLATFORM_PLUGIN_PATH", Path.Combine(qt, "plugins", "platforms"));
            Environment.SetEnvironmentVariable("PATH", runtime + Path.PathSeparator + Path.Combine(runtime, "DLLs") +
                Path.PathSeparator + Path.Combine(qt, "bin") + Path.PathSeparator + Environment.GetEnvironmentVariable("PATH"));
            var command = new StringBuilder("-B -u "); command.Append(Quote(Path.Combine(stage, "update_bridge.py")));
            command.Append(" --root "); command.Append(Quote(root));
            foreach (string arg in args) if (arg == "--check" || arg == "--smoke-test") { command.Append(' '); command.Append(arg); }
            var start = new ProcessStartInfo(python, command.ToString());
            start.WorkingDirectory = root; start.UseShellExecute = false; start.CreateNoWindow = true;
            using (var process = Process.Start(start))
            {
                process.WaitForExit();
                if (process.ExitCode != 0 && !diagnostic)
                    MessageBox.Show("兼容更新未完成。请查看 cache\\updates\\compatibility.log。", "术力口工作流助手", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return process.ExitCode;
            }
        }
        catch (Exception error)
        {
            if (!diagnostic) MessageBox.Show(error.Message, "术力口工作流助手", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        finally
        {
            // Delete only the two payload files we created. Never recursively
            // remove a computed path or touch an installed application file.
            if (stage != null)
            {
                try
                {
                    CheckDirectory(stage);
                    foreach (string name in new[] { "update_bridge.py", "update_apply.py" }) File.Delete(Path.Combine(stage, name));
                    Directory.Delete(stage, false);
                }
                catch { }
            }
        }
    }
}
