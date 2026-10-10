using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

[assembly: AssemblyTitle("Better Vocaloid Workflow Assistant")]
[assembly: AssemblyDescription("Portable desktop launcher")]
[assembly: AssemblyVersion("0.3.0.0")]
[assembly: AssemblyFileVersion("0.3.0.0")]

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
