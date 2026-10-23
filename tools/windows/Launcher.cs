using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

[assembly: AssemblyTitle("Vocaloid Workflow Assistant")]
[assembly: AssemblyDescription("Portable desktop launcher")]
[assembly: AssemblyVersion("__VWA_ASSEMBLY_VERSION__")]
[assembly: AssemblyFileVersion("__VWA_ASSEMBLY_VERSION__")]
[assembly: AssemblyInformationalVersion("__VWA_INFORMATIONAL_VERSION__")]

internal static class Launcher
{
    private static string Quote(string value)
    {
        var output = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in value)
        {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') { output.Append('\\', slashes * 2 + 1); output.Append(c); }
            else { output.Append('\\', slashes); output.Append(c); }
            slashes = 0;
        }
        output.Append('\\', slashes * 2);
        output.Append('"');
        return output.ToString();
    }

    [STAThread]
    private static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        bool diagnostic = Array.Exists(args, a => a == "--check" || a == "--smoke-test");
        try
        {
            // Only upgraded installs retain a hidden alias for existing shortcuts.
            if (String.Equals(Path.GetFileName(Application.ExecutablePath), "BVWA.exe", StringComparison.OrdinalIgnoreCase))
            {
                string canonical = Path.Combine(root, "VWA.exe");
                if (!File.Exists(canonical)) throw new FileNotFoundException("缺少 VWA.exe，请修复安装。", canonical);
                var forwarded = new StringBuilder();
                foreach (string arg in args) { forwarded.Append(Quote(arg)); forwarded.Append(' '); }
                Environment.SetEnvironmentVariable("VWA_COMPAT_LAUNCHER_PID", Process.GetCurrentProcess().Id.ToString());
                var redirect = new ProcessStartInfo(canonical, forwarded.ToString());
                redirect.WorkingDirectory = root; redirect.UseShellExecute = false; redirect.CreateNoWindow = true;
                using (var child = Process.Start(redirect))
                {
                    if (diagnostic) { child.WaitForExit(); return child.ExitCode; }
                }
                return 0;
            }
            string python = Path.Combine(root, "dependencies", "vocal2midi", "python", "pythonw.exe");
            if (!File.Exists(python))
                throw new FileNotFoundException("缺少运行库。请下载并解压完整便携包，保留 dependencies 文件夹。\n\nThe portable runtime is missing. Extract the complete package, including dependencies.");
            if (File.Exists(Path.Combine(root, "cache", "runtime-install-in-progress")))
                throw new InvalidOperationException("运行库安装尚未完成。请运行 tools\\Start-Diagnostics.bat 继续安装。\n\nRuntime installation is incomplete. Run tools\\Start-Diagnostics.bat to resume.");
            var arguments = new StringBuilder("-B -u ");
            arguments.Append(Quote(Path.Combine(root, "launch.py")));
            arguments.Append(" --desktop");
            foreach (string arg in args) { arguments.Append(' '); arguments.Append(Quote(arg)); }
            var start = new ProcessStartInfo(python, arguments.ToString());
            start.WorkingDirectory = root;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            // Inherit the process environment directly. Some hosts expose both
            // Path and PATH; Framework's case-insensitive environment dictionary
            // rejects that pair when EnvironmentVariables is first accessed.
            Environment.SetEnvironmentVariable("PYTHONUTF8", "1");
            Environment.SetEnvironmentVariable("PYTHONDONTWRITEBYTECODE", "1");
            Environment.SetEnvironmentVariable("PYTHONNOUSERSITE", "1");
            if (!diagnostic) Environment.SetEnvironmentVariable("VWA_COMPAT_LAUNCHER_PID", null);
            Environment.SetEnvironmentVariable("VWA_LAUNCHER_PID", Process.GetCurrentProcess().Id.ToString());
            Environment.SetEnvironmentVariable("BVWA_LAUNCHER_PID", Process.GetCurrentProcess().Id.ToString());
            using (var process = Process.Start(start))
            {
                process.WaitForExit();
                if (process.ExitCode != 0 && !diagnostic)
                    MessageBox.Show("助手未能正常启动或已异常退出。请查看 launcher.log，或运行 tools\\Start-Diagnostics.bat。\n\nSee launcher.log for details.", "术力口工作流助手", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return process.ExitCode;
            }
        }
        catch (Exception error)
        {
            try { File.AppendAllText(Path.Combine(root, "launcher.log"), error.ToString() + Environment.NewLine, Encoding.UTF8); }
            catch { }
            if (!diagnostic)
                MessageBox.Show(error.Message, "术力口工作流助手", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
