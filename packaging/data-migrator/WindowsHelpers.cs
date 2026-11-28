using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows;
using System.Windows.Interop;

namespace VwaDataMigrator
{
    internal static class WindowsHelpers
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
                int cap = dark ? 0x0016100E : 0x00F7F2F3;
                int txt = dark ? 0x00F5EEEE : 0x00332224;
                DwmSetWindowAttribute(hwnd, DWMWA_CAPTION_COLOR, ref cap, sizeof(int));
                DwmSetWindowAttribute(hwnd, DWMWA_TEXT_COLOR, ref txt, sizeof(int));
            }
            catch (Exception) { }
        }

        public static long GetAvailableFreeSpace(string path)
        {
            try
            {
                if (string.IsNullOrEmpty(path)) return -1;
                string root = Path.GetPathRoot(Path.GetFullPath(path));
                if (string.IsNullOrEmpty(root)) return -1;
                var drive = new DriveInfo(root);
                return drive.IsReady ? drive.AvailableFreeSpace : -1;
            }
            catch (Exception) { return -1; }
        }

        public static bool IsSameDrive(string pathA, string pathB)
        {
            try
            {
                string rA = Path.GetPathRoot(Path.GetFullPath(pathA)) ?? "";
                string rB = Path.GetPathRoot(Path.GetFullPath(pathB)) ?? "";
                return !string.IsNullOrEmpty(rA) && string.Equals(rA, rB, StringComparison.OrdinalIgnoreCase);
            }
            catch (Exception) { return false; }
        }

        public static string FormatBytes(long bytes)
        {
            if (bytes <= 0) return "0 B";
            string[] units = { "B", "KB", "MB", "GB", "TB" };
            double val = bytes;
            int idx = 0;
            while (val >= 1024.0 && idx < units.Length - 1) { val /= 1024.0; idx++; }
            return string.Format(System.Globalization.CultureInfo.InvariantCulture, idx >= 2 ? "{0:0.00} {1}" : "{0:0} {1}", val, units[idx]);
        }

        public static bool LaunchVwa(string targetDir)
        {
            string exe = Path.Combine(targetDir, "VWA.exe");
            if (!File.Exists(exe)) return false;
            Process.Start(new ProcessStartInfo(exe) { WorkingDirectory = targetDir, UseShellExecute = true });
            return true;
        }

        public static void OpenFolder(string dir)
        {
            if (!string.IsNullOrEmpty(dir) && Directory.Exists(dir))
                Process.Start(new ProcessStartInfo("explorer.exe", "\"" + dir + "\"") { UseShellExecute = true });
        }

        public static string PickFolder(Window owner, string title, string initialDir)
        {
            try
            {
                var dialog = (IFileOpenDialog)new FileOpenDialogClass();
                try
                {
                    uint options;
                    dialog.GetOptions(out options);
                    dialog.SetOptions(options | 0x00000020u | 0x00000040u); // FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM
                    dialog.SetTitle(title ?? "");
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
                                item.GetDisplayName(0x80058000u, out path);
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
    }
}
