using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Interop;
using Microsoft.Win32;

namespace VwaOnlineInstaller
{
    internal static class WindowsIntegration
    {
        private const int DWMWA_USE_IMMERSIVE_DARK_MODE = 20;
        private const int DWMWA_USE_IMMERSIVE_DARK_MODE_PRE20H1 = 19;
        private const int DWMWA_CAPTION_COLOR = 35;
        private const int DWMWA_TEXT_COLOR = 36;

        [DllImport("dwmapi.dll")]
        private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int attrValue, int attrSize);

        public static void ApplyTitlebarTheme(Window window, bool dark)
        {
            try
            {
                IntPtr hwnd = new WindowInteropHelper(window).Handle;
                if (hwnd == IntPtr.Zero) return;
                int darkVal = dark ? 1 : 0;
                if (DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ref darkVal, sizeof(int)) != 0)
                    DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE_PRE20H1, ref darkVal, sizeof(int));
                int cap = dark ? 0x0016100E : 0x00F7F2F3; // BGR COLORREF (#0E1016 / #F3F2F7)
                int txt = dark ? 0x00F5EEEE : 0x00332224;
                DwmSetWindowAttribute(hwnd, DWMWA_CAPTION_COLOR, ref cap, sizeof(int));
                DwmSetWindowAttribute(hwnd, DWMWA_TEXT_COLOR, ref txt, sizeof(int));
            }
            catch (Exception) { }
        }

        public static string DefaultInstallDir()
        {
            string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            return Path.Combine(local, "VWA");
        }

        public static long GetAvailableFreeSpace(string path)
        {
            try
            {
                string root = Path.GetPathRoot(Path.GetFullPath(path));
                if (string.IsNullOrEmpty(root)) return -1;
                var drive = new DriveInfo(root);
                return drive.IsReady ? drive.AvailableFreeSpace : -1;
            }
            catch (Exception) { return -1; }
        }

        public static string DetectExistingVersion(string dir)
        {
            try
            {
                if (string.IsNullOrEmpty(dir)) return "";
                string verPath = Path.Combine(dir, "version.json");
                if (!File.Exists(verPath)) return "";
                var ser = new JavaScriptSerializer();
                var doc = ser.DeserializeObject(File.ReadAllText(verPath, Encoding.UTF8).TrimStart('\uFEFF')) as Dictionary<string, object>;
                return ReleaseCatalog.GetString(doc, "version");
            }
            catch (Exception) { return ""; }
        }

        public static void WriteInitialConfigIfNeeded(
            string targetDir, string uiLanguage, string routeSource, string proxyMode, string proxyUrl)
        {
            try
            {
                string cfgPath = Path.Combine(targetDir, "config.json");
                string exPath = Path.Combine(targetDir, "config.example.json");
                var ser = new JavaScriptSerializer { MaxJsonLength = 4 * 1024 * 1024 };
                Dictionary<string, object> cfg = null;
                if (File.Exists(cfgPath))
                    cfg = ser.DeserializeObject(File.ReadAllText(cfgPath, Encoding.UTF8).TrimStart('\uFEFF')) as Dictionary<string, object>;
                else if (File.Exists(exPath))
                    cfg = ser.DeserializeObject(File.ReadAllText(exPath, Encoding.UTF8).TrimStart('\uFEFF')) as Dictionary<string, object>;
                if (cfg == null) cfg = new Dictionary<string, object>();

                if (!string.IsNullOrEmpty(routeSource)) cfg["download_source"] = routeSource;
                if (!string.IsNullOrEmpty(proxyMode)) cfg["network_proxy_mode"] = proxyMode;
                if (string.Equals(proxyMode, "custom", StringComparison.OrdinalIgnoreCase) && !string.IsNullOrEmpty(proxyUrl))
                    cfg["network_proxy_url"] = proxyUrl;
                var utf8NoBom = new UTF8Encoding(false);
                File.WriteAllText(cfgPath, ser.Serialize(cfg), utf8NoBom);

                // Seed appearance.json with installer language if not already customized
                string appPath = Path.Combine(targetDir, "cache", "appearance.json");
                if (!File.Exists(appPath) && !string.IsNullOrEmpty(uiLanguage))
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(appPath));
                    File.WriteAllText(appPath, ser.Serialize(new Dictionary<string, object>
                    {
                        { "ui_language", uiLanguage }
                    }), utf8NoBom);
                }
            }
            catch (Exception) { }
        }

        public static void CreateShortcutsAndUninstall(string targetDir, string version, bool startMenu, bool desktop)
        {
            string vwaExe = Path.Combine(targetDir, "VWA.exe");
            string diagBat = Path.Combine(targetDir, "tools", "Start-Diagnostics.bat");
            string iconPath = File.Exists(Path.Combine(targetDir, "assets", "app.ico"))
                ? Path.Combine(targetDir, "assets", "app.ico")
                : vwaExe;

            string startMenuDir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.Programs), "VWA");
            string desktopLnk = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "VWA.lnk");

            if (startMenu)
            {
                Directory.CreateDirectory(startMenuDir);
                CreateShortcut(Path.Combine(startMenuDir, "VWA.lnk"), vwaExe, targetDir, iconPath, "VWA - Vocaloid Workflow Assistant");
                if (File.Exists(diagBat))
                    CreateShortcut(Path.Combine(startMenuDir, "VWA Diagnostics.lnk"), diagBat, targetDir, iconPath, "VWA Diagnostics");
            }
            if (desktop)
            {
                CreateShortcut(desktopLnk, vwaExe, targetDir, iconPath, "VWA - Vocaloid Workflow Assistant");
            }

            // Write safe per-user uninstall helper (preserves user jobs/, config.json, user-presets/, accounts, optional models)
            string uninstDir = Path.Combine(targetDir, "tools");
            Directory.CreateDirectory(uninstDir);
            string uninstCmd = Path.Combine(uninstDir, "Uninstall-VWA.cmd");
            string script =
                "@echo off\r\n" +
                "chcp 65001 >nul\r\n" +
                "echo Removing VWA shortcuts and registration (preserving jobs, config.json, user-presets, accounts, and optional models)...\r\n" +
                "rmdir /s /q \"%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\VWA\" 2>nul\r\n" +
                "del /f /q \"%USERPROFILE%\\Desktop\\VWA.lnk\" 2>nul\r\n" +
                "reg delete \"HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\VWA\" /f >nul 2>&1\r\n" +
                "echo Done. Your personal data in \"" + targetDir + "\" has been preserved.\r\n";
            File.WriteAllText(uninstCmd, script, new UTF8Encoding(false));

            try
            {
                using (RegistryKey key = Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\VWA"))
                {
                    if (key != null)
                    {
                        key.SetValue("DisplayName", "VWA - Vocaloid Workflow Assistant");
                        key.SetValue("DisplayVersion", version);
                        key.SetValue("Publisher", "Transilent");
                        key.SetValue("InstallLocation", targetDir);
                        key.SetValue("DisplayIcon", iconPath);
                        key.SetValue("URLInfoAbout", "https://github.com/" + ReleaseCatalog.Repo);
                        key.SetValue("UninstallString", "cmd.exe /c \"" + uninstCmd + "\"");
                        key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                        key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                    }
                }
            }
            catch (Exception) { }
        }

        public static bool LaunchVwa(string targetDir)
        {
            string exe = Path.Combine(targetDir, "VWA.exe");
            if (!File.Exists(exe)) return false;
            var psi = new ProcessStartInfo(exe)
            {
                WorkingDirectory = targetDir,
                UseShellExecute = true
            };
            Process.Start(psi);
            return true;
        }

        public static void OpenFolder(string dir)
        {
            if (Directory.Exists(dir))
                Process.Start(new ProcessStartInfo("explorer.exe", "\"" + dir + "\"") { UseShellExecute = true });
        }

        public static void OpenUrl(string url)
        {
            if (!string.IsNullOrEmpty(url) && (url.StartsWith("https://", StringComparison.OrdinalIgnoreCase) || url.StartsWith("http://", StringComparison.OrdinalIgnoreCase)))
                Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
        }

        // --- Modern Vista+ Explorer Folder Picker (COM IFileOpenDialog) ---
        public static string PickFolder(Window owner, string initialDir)
        {
            try
            {
                var dialog = (IFileOpenDialog)new FileOpenDialogClass();
                try
                {
                    uint options;
                    dialog.GetOptions(out options);
                    dialog.SetOptions(options | 0x00000020u | 0x00000040u); // FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM
                    dialog.SetTitle(I18n.T("install_dir"));
                    if (!string.IsNullOrEmpty(initialDir) && Directory.Exists(initialDir))
                    {
                        IShellItem initItem;
                        Guid iid = typeof(IShellItem).GUID;
                        if (SHCreateItemFromParsingName(initialDir, IntPtr.Zero, ref iid, out initItem) == 0 && initItem != null)
                        {
                            dialog.SetFolder(initItem);
                            Marshal.ReleaseComObject(initItem);
                        }
                    }
                    IntPtr hwnd = owner != null ? new WindowInteropHelper(owner).Handle : IntPtr.Zero;
                    if (dialog.Show(hwnd) == 0)
                    {
                        IShellItem item;
                        dialog.GetResult(out item);
                        if (item != null)
                        {
                            try
                            {
                                string path;
                                item.GetDisplayName(0x80058000u, out path); // SIGDN_FILESYSPATH
                                return path ?? "";
                            }
                            finally { Marshal.ReleaseComObject(item); }
                        }
                    }
                    return "";
                }
                finally { Marshal.ReleaseComObject(dialog); }
            }
            catch (Exception) { return ""; }
        }

        // --- Native COM IShellLinkW shortcut creator (zero WScript dependency) ---
        private static void CreateShortcut(string lnkPath, string targetPath, string workingDir, string iconPath, string description)
        {
            try
            {
                var link = (IShellLinkW)new ShellLinkClass();
                try
                {
                    link.SetPath(targetPath);
                    link.SetWorkingDirectory(workingDir);
                    link.SetDescription(description);
                    if (!string.IsNullOrEmpty(iconPath)) link.SetIconLocation(iconPath, 0);
                    ((IPersistFile)link).Save(lnkPath, false);
                }
                finally { Marshal.ReleaseComObject(link); }
            }
            catch (Exception) { }
        }

        [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
        private static extern int SHCreateItemFromParsingName(string pszPath, IntPtr pbc, ref Guid riid, out IShellItem ppv);

        [ComImport, Guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")]
        private class FileOpenDialogClass { }

        [ComImport, Guid("d57c7288-d4ad-4768-be02-9d969532d960"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IFileOpenDialog
        {
            [PreserveSig] int Show(IntPtr parent);
            void SetFileTypes(uint cFileTypes, IntPtr rgFilterSpec);
            void SetFileTypeIndex(uint iFileType);
            void GetFileTypeIndex(out uint piFileType);
            void Advise(IntPtr pfde, out uint pdwCookie);
            void Unadvise(uint dwCookie);
            void SetOptions(uint fos);
            void GetOptions(out uint pfos);
            void SetDefaultFolder(IShellItem psi);
            void SetFolder(IShellItem psi);
            void GetFolder(out IShellItem ppsi);
            void GetCurrentSelection(out IShellItem ppsi);
            void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string pszName);
            void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string pszName);
            void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string pszTitle);
            void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string pszText);
            void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string pszLabel);
            void GetResult(out IShellItem ppsi);
        }

        [ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IShellItem
        {
            void BindToHandler(IntPtr pbc, ref Guid bhid, ref Guid riid, out IntPtr ppv);
            void GetParent(out IShellItem ppsi);
            void GetDisplayName(uint sigdnName, [MarshalAs(UnmanagedType.LPWStr)] out string ppszName);
            void GetAttributes(uint sfgaoMask, out uint psfgaoAttribs);
            void Compare(IShellItem psi, uint hint, out int piOrder);
        }

        [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
        private class ShellLinkClass { }

        [ComImport, Guid("000214F9-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IShellLinkW
        {
            void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszFile, int cchMaxPath, IntPtr pfd, uint fFlags);
            void GetIDList(out IntPtr ppidl);
            void SetIDList(IntPtr pidl);
            void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszName, int cchMaxName);
            void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);
            void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszDir, int cchMaxPath);
            void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
            void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszArgs, int cchMaxPath);
            void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
            void GetHotkey(out ushort pwHotkey);
            void SetHotkey(ushort wHotkey);
            void GetShowCmd(out int piShowCmd);
            void SetShowCmd(int iShowCmd);
            void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszIconPath, int cchIconPath, out int piIcon);
            void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
            void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, uint dwReserved);
            void Resolve(IntPtr hwnd, uint fFlags);
            void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
        }
    }
}
