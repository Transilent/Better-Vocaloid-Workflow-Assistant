using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;

namespace VwaDataMigrator
{
    internal sealed class InstallProbe
    {
        public bool Exists;
        public bool HasLauncher;
        public bool HasConfigOrJobs;
        public string Version = "";
        public string Variant = "";
        public bool HasAllQwen;
    }

    internal sealed class CategoryStats
    {
        public string Key;
        public int FileCount;
        public long TotalBytes;
        public bool AlreadyInTarget;
    }

    internal sealed class ScanSummary
    {
        public InstallProbe SourceInfo = new InstallProbe();
        public InstallProbe TargetInfo = new InstallProbe();
        public Dictionary<string, CategoryStats> Categories = new Dictionary<string, CategoryStats>();
    }

    internal sealed class MigrationOptions
    {
        public string SourceDir = "";
        public string TargetDir = "";
        public bool MoveMode;
        public bool IncludeConfig = true;
        public bool IncludeJobs = true;
        public bool IncludePresets = true;
        public bool IncludeAccounts = true;
        public bool IncludePublish = true;
        public bool IncludeOptional = true;
        public bool IncludeQwen = true;
    }

    internal sealed class MigrationProgress
    {
        public int DoneFiles;
        public int TotalFiles;
        public long DoneBytes;
        public long TotalBytes;
        public double Percent;
        public double SpeedBps;
        public string Message = "";
    }

    internal sealed class MigrationResult
    {
        public int FilesTransferred;
        public int FilesSkipped;
        public long BytesTransferred;
        public bool ConfigMerged;
        public int StateFilesMigrated;
        public int QwenWeightsTransferred;
        public string ReceiptPath = "";
    }

    internal static class MigrationEngine
    {
        public static readonly string[] QwenWeightFiles =
        {
            "dependencies\\vocal2midi\\experiments\\Qwen3-ASR-1.7B-dml\\qwen3_asr_encoder_backend.fp16.onnx",
            "dependencies\\vocal2midi\\experiments\\Qwen3-ASR-1.7B-dml\\qwen3_asr_encoder_frontend.fp16.onnx",
            "dependencies\\vocal2midi\\experiments\\Qwen3-ASR-1.7B-dml\\qwen3_asr_llm.f16.gguf"
        };

        public static readonly string[] UserStateJsonFiles =
        {
            "cache\\workspace.json",
            "cache\\appearance.json",
            "cache\\ui-state.json",
            "cache\\recent-items.json"
        };

        private static readonly HashSet<string> InternalConfigKeys = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "python", "vocal2midi", "ffmpeg", "mdx_model", "mdx_metadata",
            "chorus_model", "chorus_metadata", "publish_browser_executable"
        };

        public static string LongPath(string p)
        {
            string full = Path.GetFullPath(p);
            if (full.StartsWith("\\\\?\\", StringComparison.Ordinal)) return full;
            if (full.Length < 240) return full;
            if (full.StartsWith("\\\\", StringComparison.Ordinal)) return "\\\\?\\UNC\\" + full.Substring(2);
            return "\\\\?\\" + full;
        }

        public static InstallProbe InspectInstall(string dir)
        {
            var probe = new InstallProbe();
            if (string.IsNullOrEmpty(dir)) return probe;
            try
            {
                string full = Path.GetFullPath(dir.Trim());
                if (!Directory.Exists(full)) return probe;
                probe.Exists = true;
                probe.HasLauncher = File.Exists(Path.Combine(full, "VWA.exe")) || File.Exists(Path.Combine(full, "BVWA.exe"));
                probe.HasConfigOrJobs = File.Exists(Path.Combine(full, "config.json"))
                    || Directory.Exists(Path.Combine(full, "jobs"))
                    || Directory.Exists(Path.Combine(full, "user-presets"))
                    || Directory.Exists(Path.Combine(full, "dependencies\\optional"));

                string verPath = Path.Combine(full, "version.json");
                if (File.Exists(verPath))
                {
                    var ser = new JavaScriptSerializer();
                    var doc = ser.DeserializeObject(File.ReadAllText(verPath, Encoding.UTF8).TrimStart('\uFEFF')) as Dictionary<string, object>;
                    object v;
                    if (doc != null && doc.TryGetValue("version", out v) && v != null)
                        probe.Version = Convert.ToString(v, CultureInfo.InvariantCulture) ?? "";
                }

                int qwenCount = 0;
                foreach (string rel in QwenWeightFiles)
                {
                    if (File.Exists(Path.Combine(full, rel))) qwenCount++;
                }
                probe.HasAllQwen = (qwenCount == QwenWeightFiles.Length);
                if (probe.HasLauncher || !string.IsNullOrEmpty(probe.Version))
                    probe.Variant = probe.HasAllQwen ? "Full" : "JPN";
            }
            catch (Exception) { }
            return probe;
        }

        public static ScanSummary Scan(string sourceDir, string targetDir)
        {
            var summary = new ScanSummary();
            summary.SourceInfo = InspectInstall(sourceDir);
            summary.TargetInfo = InspectInstall(targetDir);

            string[] keys = { "config", "jobs", "presets", "accounts", "publish", "optional", "qwen" };
            foreach (string k in keys) summary.Categories[k] = new CategoryStats { Key = k };

            if (!summary.SourceInfo.Exists) return summary;
            string srcRoot = Path.GetFullPath(sourceDir.Trim());
            string dstRoot = summary.TargetInfo.Exists ? Path.GetFullPath(targetDir.Trim()) : "";

            // 1. Config & UI state JSONs
            var cfgStats = summary.Categories["config"];
            string oldCfg = Path.Combine(srcRoot, "config.json");
            if (File.Exists(oldCfg))
            {
                cfgStats.FileCount++;
                cfgStats.TotalBytes += new FileInfo(oldCfg).Length;
            }
            foreach (string rel in UserStateJsonFiles)
            {
                string f = Path.Combine(srcRoot, rel);
                if (File.Exists(f))
                {
                    cfgStats.FileCount++;
                    cfgStats.TotalBytes += new FileInfo(f).Length;
                }
            }

            // 2. Directory trees
            ScanDirInto(Path.Combine(srcRoot, "jobs"), summary.Categories["jobs"]);
            ScanDirInto(Path.Combine(srcRoot, "user-presets"), summary.Categories["presets"]);
            ScanDirInto(Path.Combine(srcRoot, "cache\\accounts"), summary.Categories["accounts"]);
            ScanDirInto(Path.Combine(srcRoot, "publish-packages"), summary.Categories["publish"]);
            ScanDirInto(Path.Combine(srcRoot, "dependencies\\optional"), summary.Categories["optional"]);

            // 3. Qwen3-ASR canonical weights
            var qStats = summary.Categories["qwen"];
            int presentInSrc = 0;
            int missingInDst = 0;
            foreach (string rel in QwenWeightFiles)
            {
                string sf = Path.Combine(srcRoot, rel);
                if (File.Exists(sf))
                {
                    presentInSrc++;
                    bool inDst = !string.IsNullOrEmpty(dstRoot) && File.Exists(Path.Combine(dstRoot, rel));
                    if (!inDst)
                    {
                        missingInDst++;
                        qStats.FileCount++;
                        qStats.TotalBytes += new FileInfo(sf).Length;
                    }
                }
            }
            if (presentInSrc > 0 && missingInDst == 0 && !string.IsNullOrEmpty(dstRoot))
                qStats.AlreadyInTarget = true;

            return summary;
        }

        private static void ScanDirInto(string dirPath, CategoryStats stats)
        {
            if (!Directory.Exists(dirPath)) return;
            var stack = new Stack<string>();
            stack.Push(dirPath);
            while (stack.Count > 0)
            {
                string cur = stack.Pop();
                try
                {
                    foreach (string file in Directory.GetFiles(LongPath(cur)))
                    {
                        try
                        {
                            stats.FileCount++;
                            stats.TotalBytes += new FileInfo(file).Length;
                        }
                        catch (Exception) { }
                    }
                    foreach (string sub in Directory.GetDirectories(LongPath(cur)))
                        stack.Push(sub);
                }
                catch (Exception) { }
            }
        }

        public static void ValidatePaths(string sourceDir, string targetDir)
        {
            if (string.IsNullOrEmpty((sourceDir ?? "").Trim()) || string.IsNullOrEmpty((targetDir ?? "").Trim()))
                throw new ArgumentException(I18n.T("err_select_both"));
            string sFull = Path.GetFullPath(sourceDir.Trim()).TrimEnd('\\');
            string tFull = Path.GetFullPath(targetDir.Trim()).TrimEnd('\\');
            if (!Directory.Exists(sFull))
                throw new DirectoryNotFoundException(I18n.T("err_source_missing"));
            if (!Directory.Exists(tFull))
                throw new DirectoryNotFoundException(I18n.T("err_target_missing"));
            if (string.Equals(sFull, tFull, StringComparison.OrdinalIgnoreCase))
                throw new ArgumentException(I18n.T("err_same_dir"));
            if (tFull.StartsWith(sFull + "\\", StringComparison.OrdinalIgnoreCase)
                || sFull.StartsWith(tFull + "\\", StringComparison.OrdinalIgnoreCase))
                throw new ArgumentException(I18n.T("err_nested_dir"));

            if (IsFolderLockedByVwa(sFull) || IsFolderLockedByVwa(tFull))
                throw new IOException(I18n.T("err_vwa_running"));
        }

        private static bool IsFolderLockedByVwa(string root)
        {
            string[] lockNames = { "cache\\VWA.lock", "cache\\BVWA.lock" };
            foreach (string rel in lockNames)
            {
                string lockPath = Path.Combine(root, rel);
                if (!File.Exists(lockPath)) continue;
                try
                {
                    using (var fs = new FileStream(lockPath, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
                    {
                        // Lock file exists but is stale (not held by an active process)
                    }
                }
                catch (IOException)
                {
                    return true;
                }
                catch (UnauthorizedAccessException)
                {
                    return true;
                }
            }
            return false;
        }

        private sealed class TransferItem
        {
            public string Category;
            public string SourcePath;
            public string TargetPath;
            public string DisplayName;
            public long Bytes;
        }

        public static Task<MigrationResult> ExecuteAsync(
            MigrationOptions opts, Action<MigrationProgress> report, CancellationToken ct)
        {
            return Task.Run(delegate
            {
                ValidatePaths(opts.SourceDir, opts.TargetDir);
                string oldRoot = Path.GetFullPath(opts.SourceDir.Trim());
                string newRoot = Path.GetFullPath(opts.TargetDir.Trim());
                bool sameDriveMove = opts.MoveMode && WindowsHelpers.IsSameDrive(oldRoot, newRoot);

                var plan = new List<TransferItem>();
                long totalTransferBytes = 0;

                if (opts.IncludeJobs) CollectTree(oldRoot, newRoot, "jobs", "jobs", plan);
                if (opts.IncludePresets) CollectTree(oldRoot, newRoot, "user-presets", "presets", plan);
                if (opts.IncludeAccounts) CollectTree(oldRoot, newRoot, "cache\\accounts", "accounts", plan);
                if (opts.IncludePublish) CollectTree(oldRoot, newRoot, "publish-packages", "publish", plan);
                if (opts.IncludeOptional) CollectTree(oldRoot, newRoot, "dependencies\\optional", "optional", plan);
                if (opts.IncludeQwen)
                {
                    foreach (string rel in QwenWeightFiles)
                    {
                        string sf = Path.Combine(oldRoot, rel);
                        string df = Path.Combine(newRoot, rel);
                        if (File.Exists(sf) && !File.Exists(df))
                        {
                            plan.Add(new TransferItem
                            {
                                Category = "qwen",
                                SourcePath = sf,
                                TargetPath = df,
                                DisplayName = Path.GetFileName(sf),
                                Bytes = new FileInfo(sf).Length
                            });
                        }
                    }
                }

                bool hasConfigWork = opts.IncludeConfig && (File.Exists(Path.Combine(oldRoot, "config.json"))
                    || AnyUserStatePresent(oldRoot));

                if (plan.Count == 0 && !hasConfigWork)
                    throw new InvalidOperationException(I18n.T("err_nothing_selected"));

                foreach (var it in plan) totalTransferBytes += it.Bytes;

                // Preflight disk space check on target drive
                long freeBytes = WindowsHelpers.GetAvailableFreeSpace(newRoot);
                long needBytes = sameDriveMove ? (64L * 1024L * 1024L) : (totalTransferBytes + 128L * 1024L * 1024L);
                if (freeBytes >= 0 && freeBytes < needBytes)
                {
                    throw new IOException(I18n.F("err_disk_space",
                        WindowsHelpers.FormatBytes(freeBytes), WindowsHelpers.FormatBytes(needBytes)));
                }

                var result = new MigrationResult();
                var dirCounts = new Dictionary<string, int>();

                // Step 1: Smart-merge config.json & user state JSONs (always copied non-destructively even in Move mode)
                if (hasConfigWork)
                {
                    ct.ThrowIfCancellationRequested();
                    report(new MigrationProgress
                    {
                        DoneFiles = 0, TotalFiles = Math.Max(1, plan.Count),
                        DoneBytes = 0, TotalBytes = Math.Max(1, totalTransferBytes),
                        Percent = 0, Message = I18n.T("status_migrating_cfg")
                    });
                    result.ConfigMerged = MergeConfigJson(oldRoot, newRoot);
                    result.StateFilesMigrated = MigrateUserStateJsons(oldRoot, newRoot);
                }

                // Step 2: Transfer all selected files with live byte progress & cancel support
                var sw = Stopwatch.StartNew();
                long doneBytes = 0;
                long speedBaseBytes = 0;
                double speedBaseSec = 0;
                double speedBps = 0;
                byte[] copyBuf = new byte[1024 * 1024];

                for (int i = 0; i < plan.Count; i++)
                {
                    ct.ThrowIfCancellationRequested();
                    var item = plan[i];
                    string dstLong = LongPath(item.TargetPath);
                    string srcLong = LongPath(item.SourcePath);

                    if (File.Exists(dstLong))
                    {
                        result.FilesSkipped++;
                        doneBytes += item.Bytes;
                        EmitProgress(report, i + 1, plan.Count, doneBytes, totalTransferBytes, speedBps, item.DisplayName);
                        continue;
                    }

                    string dstParent = Path.GetDirectoryName(item.TargetPath);
                    if (!string.IsNullOrEmpty(dstParent)) Directory.CreateDirectory(LongPath(dstParent));

                    if (sameDriveMove)
                    {
                        File.Move(srcLong, dstLong);
                        doneBytes += item.Bytes;
                    }
                    else
                    {
                        string tmpDst = LongPath(item.TargetPath + ".vwa-migrate.tmp");
                        try
                        {
                            using (var inFs = new FileStream(srcLong, FileMode.Open, FileAccess.Read, FileShare.Read, 1024 * 1024))
                            using (var outFs = new FileStream(tmpDst, FileMode.Create, FileAccess.Write, FileShare.None, 1024 * 1024))
                            {
                                int n;
                                while ((n = inFs.Read(copyBuf, 0, copyBuf.Length)) > 0)
                                {
                                    ct.ThrowIfCancellationRequested();
                                    outFs.Write(copyBuf, 0, n);
                                    doneBytes += n;
                                    double nowSec = sw.Elapsed.TotalSeconds;
                                    if (nowSec - speedBaseSec >= 0.35)
                                    {
                                        speedBps = (doneBytes - speedBaseBytes) / (nowSec - speedBaseSec);
                                        speedBaseBytes = doneBytes;
                                        speedBaseSec = nowSec;
                                        EmitProgress(report, i, plan.Count, doneBytes, totalTransferBytes, speedBps, item.DisplayName);
                                    }
                                }
                            }
                            try { File.SetLastWriteTimeUtc(tmpDst, File.GetLastWriteTimeUtc(srcLong)); } catch (Exception) { }
                            File.Move(tmpDst, dstLong);
                            if (opts.MoveMode)
                            {
                                try { File.Delete(srcLong); } catch (Exception) { }
                            }
                        }
                        catch (Exception)
                        {
                            try { if (File.Exists(tmpDst)) File.Delete(tmpDst); } catch (Exception) { }
                            throw;
                        }
                    }

                    result.FilesTransferred++;
                    result.BytesTransferred += item.Bytes;
                    if (item.Category == "qwen") result.QwenWeightsTransferred++;
                    int curCount;
                    dirCounts.TryGetValue(item.Category, out curCount);
                    dirCounts[item.Category] = curCount + 1;

                    if ((i + 1) % 5 == 0 || i + 1 == plan.Count)
                        EmitProgress(report, i + 1, plan.Count, doneBytes, totalTransferBytes, speedBps, item.DisplayName);
                }

                // Step 3: Write migration-receipt.json in target cache/updates/
                result.ReceiptPath = WriteReceipt(oldRoot, newRoot, opts.MoveMode ? "move" : "copy", result, dirCounts);

                report(new MigrationProgress
                {
                    DoneFiles = plan.Count, TotalFiles = Math.Max(1, plan.Count),
                    DoneBytes = Math.Max(1, totalTransferBytes), TotalBytes = Math.Max(1, totalTransferBytes),
                    Percent = 100.0, SpeedBps = 0,
                    Message = I18n.F("status_done", result.FilesTransferred, WindowsHelpers.FormatBytes(result.BytesTransferred), result.FilesSkipped)
                });
                return result;
            }, ct);
        }

        private static void EmitProgress(
            Action<MigrationProgress> report, int doneFiles, int totalFiles, long doneBytes, long totalBytes, double speedBps, string name)
        {
            double pct = totalBytes > 0
                ? Math.Min(99.5, (doneBytes * 100.0) / totalBytes)
                : Math.Min(99.5, (doneFiles * 100.0) / Math.Max(1, totalFiles));
            report(new MigrationProgress
            {
                DoneFiles = doneFiles,
                TotalFiles = Math.Max(1, totalFiles),
                DoneBytes = doneBytes,
                TotalBytes = Math.Max(1, totalBytes),
                Percent = pct,
                SpeedBps = speedBps,
                Message = I18n.F("status_copying", doneFiles, Math.Max(1, totalFiles), name)
            });
        }

        private static bool AnyUserStatePresent(string root)
        {
            foreach (string rel in UserStateJsonFiles)
                if (File.Exists(Path.Combine(root, rel))) return true;
            return false;
        }

        private static void CollectTree(string oldRoot, string newRoot, string relDir, string category, List<TransferItem> plan)
        {
            string srcBase = Path.Combine(oldRoot, relDir);
            if (!Directory.Exists(srcBase)) return;
            string dstBase = Path.Combine(newRoot, relDir);
            var stack = new Stack<string>();
            stack.Push(srcBase);
            while (stack.Count > 0)
            {
                string cur = stack.Pop();
                string relSub = cur.Length > srcBase.Length ? cur.Substring(srcBase.Length).TrimStart('\\') : "";
                foreach (string file in Directory.GetFiles(LongPath(cur)))
                {
                    string fname = Path.GetFileName(file);
                    string srcFile = string.IsNullOrEmpty(relSub) ? Path.Combine(srcBase, fname) : Path.Combine(srcBase, relSub, fname);
                    string dstFile = string.IsNullOrEmpty(relSub) ? Path.Combine(dstBase, fname) : Path.Combine(dstBase, relSub, fname);
                    long len = 0;
                    try { len = new FileInfo(LongPath(srcFile)).Length; } catch (Exception) { }
                    plan.Add(new TransferItem
                    {
                        Category = category,
                        SourcePath = srcFile,
                        TargetPath = dstFile,
                        DisplayName = relDir + "\\" + (string.IsNullOrEmpty(relSub) ? fname : (relSub + "\\" + fname)),
                        Bytes = len
                    });
                }
                foreach (string sub in Directory.GetDirectories(LongPath(cur)))
                {
                    string subName = Path.GetFileName(sub);
                    stack.Push(string.IsNullOrEmpty(relSub) ? Path.Combine(srcBase, subName) : Path.Combine(srcBase, relSub, subName));
                }
            }
        }

        private static bool MergeConfigJson(string oldRoot, string newRoot)
        {
            string oldCfgPath = Path.Combine(oldRoot, "config.json");
            if (!File.Exists(oldCfgPath)) return false;

            string newCfgPath = Path.Combine(newRoot, "config.json");
            string newExPath = Path.Combine(newRoot, "config.example.json");
            var ser = new JavaScriptSerializer { MaxJsonLength = 8 * 1024 * 1024 };

            var oldCfg = ser.DeserializeObject(File.ReadAllText(oldCfgPath, Encoding.UTF8).TrimStart('\uFEFF')) as Dictionary<string, object>;
            if (oldCfg == null) return false;

            string baseSrc = File.Exists(newExPath) ? newExPath : (File.Exists(newCfgPath) ? newCfgPath : null);
            var merged = baseSrc != null
                ? (ser.DeserializeObject(File.ReadAllText(baseSrc, Encoding.UTF8).TrimStart('\uFEFF')) as Dictionary<string, object> ?? new Dictionary<string, object>())
                : new Dictionary<string, object>();

            foreach (var kv in oldCfg)
            {
                if (!InternalConfigKeys.Contains(kv.Key))
                    merged[kv.Key] = RewritePathsRecursive(kv.Value, oldRoot, newRoot);
            }

            if (File.Exists(newCfgPath))
            {
                string bak = Path.Combine(newRoot, "config.json.before-migrate.bak");
                if (!File.Exists(bak))
                    try { File.Copy(newCfgPath, bak, false); } catch (Exception) { }
            }

            string tmp = newCfgPath + ".migrate.tmp";
            File.WriteAllText(tmp, FormatJsonPretty(ser.Serialize(merged)) + "\n", new UTF8Encoding(false));
            if (File.Exists(newCfgPath)) File.Delete(newCfgPath);
            File.Move(tmp, newCfgPath);
            return true;
        }

        private static int MigrateUserStateJsons(string oldRoot, string newRoot)
        {
            int count = 0;
            var ser = new JavaScriptSerializer { MaxJsonLength = 4 * 1024 * 1024 };
            var utf8 = new UTF8Encoding(false);
            foreach (string rel in UserStateJsonFiles)
            {
                string sf = Path.Combine(oldRoot, rel);
                string df = Path.Combine(newRoot, rel);
                if (!File.Exists(sf) || File.Exists(df)) continue;
                try
                {
                    string raw = File.ReadAllText(sf, Encoding.UTF8).TrimStart('\uFEFF');
                    object parsed = ser.DeserializeObject(raw);
                    object rewritten = RewritePathsRecursive(parsed, oldRoot, newRoot);
                    string dir = Path.GetDirectoryName(df);
                    if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
                    File.WriteAllText(df, FormatJsonPretty(ser.Serialize(rewritten)) + "\n", utf8);
                    count++;
                }
                catch (Exception)
                {
                    try
                    {
                        string dir = Path.GetDirectoryName(df);
                        if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
                        File.Copy(sf, df, false);
                        count++;
                    }
                    catch (Exception) { }
                }
            }
            return count;
        }

        private static object RewritePathsRecursive(object obj, string oldRoot, string newRoot)
        {
            if (obj == null) return null;
            var dict = obj as Dictionary<string, object>;
            if (dict != null)
            {
                var copy = new Dictionary<string, object>(dict.Count);
                foreach (var kv in dict) copy[kv.Key] = RewritePathsRecursive(kv.Value, oldRoot, newRoot);
                return copy;
            }
            var arr = obj as object[];
            if (arr != null)
            {
                var copy = new object[arr.Length];
                for (int i = 0; i < arr.Length; i++) copy[i] = RewritePathsRecursive(arr[i], oldRoot, newRoot);
                return copy;
            }
            var list = obj as ArrayList;
            if (list != null)
            {
                var copy = new ArrayList(list.Count);
                foreach (object item in list) copy.Add(RewritePathsRecursive(item, oldRoot, newRoot));
                return copy;
            }
            string s = obj as string;
            if (s != null && s.Length > 2 && !s.StartsWith("@/", StringComparison.Ordinal))
            {
                string normOld = Path.GetFullPath(oldRoot).TrimEnd('\\');
                string normNew = Path.GetFullPath(newRoot).TrimEnd('\\');
                string candidate = s.Replace('/', '\\');
                if (candidate.Equals(normOld, StringComparison.OrdinalIgnoreCase))
                    return normNew;
                if (candidate.StartsWith(normOld + "\\", StringComparison.OrdinalIgnoreCase))
                    return normNew + candidate.Substring(normOld.Length);
            }
            return obj;
        }

        private static string WriteReceipt(
            string oldRoot, string newRoot, string mode, MigrationResult res, Dictionary<string, int> dirCounts)
        {
            var srcInfo = InspectInstall(oldRoot);
            var dstInfo = InspectInstall(newRoot);
            var receipt = new Dictionary<string, object>
            {
                { "schema_version", 1 },
                { "tool", "VWA-Data-Migrator" },
                { "mode", mode },
                { "migrated_at", DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture) },
                { "source_root", oldRoot },
                { "source_version", string.IsNullOrEmpty(srcInfo.Version) ? "unknown" : srcInfo.Version },
                { "target_root", newRoot },
                { "target_version", string.IsNullOrEmpty(dstInfo.Version) ? "unknown" : dstInfo.Version },
                { "summary", new Dictionary<string, object>
                    {
                        { "config_migrated", res.ConfigMerged },
                        { "state_files_migrated", res.StateFilesMigrated },
                        { "files_transferred", res.FilesTransferred },
                        { "files_skipped", res.FilesSkipped },
                        { "bytes_transferred", res.BytesTransferred },
                        { "qwen_weights_migrated", res.QwenWeightsTransferred },
                        { "categories", dirCounts }
                    }
                }
            };
            string receiptPath = Path.Combine(newRoot, "cache\\updates\\migration-receipt.json");
            Directory.CreateDirectory(Path.GetDirectoryName(receiptPath));
            var ser = new JavaScriptSerializer();
            File.WriteAllText(receiptPath, FormatJsonPretty(ser.Serialize(receipt)) + "\n", new UTF8Encoding(false));
            return receiptPath;
        }

        private static string FormatJsonPretty(string json)
        {
            if (string.IsNullOrEmpty(json)) return "{}";
            var sb = new StringBuilder(json.Length + 128);
            bool inString = false;
            bool escaped = false;
            int indent = 0;
            for (int i = 0; i < json.Length; i++)
            {
                char ch = json[i];
                if (inString)
                {
                    sb.Append(ch);
                    if (escaped) escaped = false;
                    else if (ch == '\\') escaped = true;
                    else if (ch == '"') inString = false;
                    continue;
                }
                if (ch == '"') { inString = true; sb.Append(ch); }
                else if (ch == '{' || ch == '[')
                {
                    sb.Append(ch);
                    if (i + 1 < json.Length && (json[i + 1] == '}' || json[i + 1] == ']')) continue;
                    sb.Append('\n');
                    indent++;
                    sb.Append(' ', indent * 2);
                }
                else if (ch == '}' || ch == ']')
                {
                    if (i > 0 && (json[i - 1] == '{' || json[i - 1] == '[')) { sb.Append(ch); continue; }
                    sb.Append('\n');
                    indent = Math.Max(0, indent - 1);
                    sb.Append(' ', indent * 2);
                    sb.Append(ch);
                }
                else if (ch == ',')
                {
                    sb.Append(",\n");
                    sb.Append(' ', indent * 2);
                }
                else if (ch == ':') sb.Append(": ");
                else if (!char.IsWhiteSpace(ch)) sb.Append(ch);
            }
            return sb.ToString();
        }
    }
}
