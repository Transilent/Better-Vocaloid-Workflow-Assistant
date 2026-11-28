using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;

namespace VwaOnlineInstaller
{
    internal sealed class InstallProgress
    {
        public int Stage; // 0=idle, 1=download, 2=verify/merge, 3=extract, 4=finish
        public long DoneBytes;
        public long TotalBytes;
        public double Percent;
        public double SpeedBps;
        public string ActiveRoute;
        public int Workers;
        public string Message;
    }

    internal sealed class InstallOptions
    {
        public string Variant = "full";
        public string TargetDir;
        public string RouteSource = "auto";
        public string ProxyMode = "system";
        public string ProxyUrl = "";
        public int Workers = 4;
        public int ChunkBytes = 16 * 1024 * 1024;
        public long ParallelMinBytes = 8 * 1024 * 1024;
        public bool CreateStartMenu = true;
        public bool CreateDesktop = true;
        public bool LaunchAfter = true;
        public string ManifestBaseOverride = "";
    }

    internal static class DownloadEngine
    {
        private static readonly string[] PreservedDirs =
        {
            "jobs",
            "user-presets",
            "publish-packages",
            "cache\\accounts",
            "dependencies\\optional"
        };

        private static readonly string[] QwenWeightFiles =
        {
            "dependencies\\vocal2midi\\experiments\\Qwen3-ASR-1.7B-dml\\qwen3_asr_1.7b_q4_k_m.gguf",
            "dependencies\\vocal2midi\\experiments\\Qwen3-ASR-1.7B-dml\\audio_encoder_int8.onnx",
            "dependencies\\vocal2midi\\experiments\\Qwen3-ASR-1.7B-dml\\token_embeddings.npy"
        };

        private static readonly HashSet<string> InternalConfigKeys = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "python", "vocal2midi", "ffmpeg", "mdx_model", "mdx_metadata",
            "chorus_model", "chorus_metadata", "publish_browser_executable"
        };

        static DownloadEngine()
        {
            // Enable TLS 1.2 (3072) and TLS 1.3 (12288) on .NET Framework 4.8.
            try { ServicePointManager.SecurityProtocol = (SecurityProtocolType)3072 | (SecurityProtocolType)12288; }
            catch (Exception) { ServicePointManager.SecurityProtocol = (SecurityProtocolType)3072; }
            ServicePointManager.DefaultConnectionLimit = 16;
            ServicePointManager.Expect100Continue = false;
        }

        public static HttpClient CreateHttpClient(string proxyMode, string proxyUrl)
        {
            var handler = new HttpClientHandler
            {
                AllowAutoRedirect = true,
                AutomaticDecompression = DecompressionMethods.None
            };
            string mode = (proxyMode ?? "system").Trim().ToLowerInvariant();
            if (mode == "direct")
            {
                handler.UseProxy = false;
                handler.Proxy = null;
            }
            else if (mode == "custom")
            {
                Uri parsed;
                if (!Uri.TryCreate((proxyUrl ?? "").Trim(), UriKind.Absolute, out parsed)
                    || (parsed.Scheme != Uri.UriSchemeHttp && parsed.Scheme != Uri.UriSchemeHttps))
                {
                    handler.Dispose();
                    throw new ArgumentException(I18n.T("err_invalid_proxy"));
                }
                handler.UseProxy = true;
                handler.Proxy = new WebProxy(parsed);
            }
            else
            {
                handler.UseProxy = true;
                handler.Proxy = WebRequest.DefaultWebProxy;
            }
            var client = new HttpClient(handler);
            client.Timeout = Timeout.InfiniteTimeSpan;
            client.DefaultRequestHeaders.TryAddWithoutValidation("User-Agent", ReleaseCatalog.UserAgent);
            return client;
        }

        public static string ComputeSha256(string filePath, CancellationToken ct)
        {
            using (var sha = SHA256.Create())
            using (var fs = new FileStream(filePath, FileMode.Open, FileAccess.Read, FileShare.Read, 1024 * 1024))
            {
                byte[] buffer = new byte[2 * 1024 * 1024];
                int read;
                while ((read = fs.Read(buffer, 0, buffer.Length)) > 0)
                {
                    ct.ThrowIfCancellationRequested();
                    sha.TransformBlock(buffer, 0, read, null, 0);
                }
                sha.TransformFinalBlock(new byte[0], 0, 0);
                return BytesToHex(sha.Hash);
            }
        }

        private static string BytesToHex(byte[] hash)
        {
            var sb = new StringBuilder(hash.Length * 2);
            for (int i = 0; i < hash.Length; i++) sb.Append(hash[i].ToString("x2", CultureInfo.InvariantCulture));
            return sb.ToString();
        }

        private sealed class ProbeResult
        {
            public string Url;
            public bool Ok;
            public bool SupportsRange;
            public double ElapsedMs;
        }

        public static async Task<List<KeyValuePair<string, bool>>> ProbeUrlsFastestFirstAsync(
            HttpClient client, List<string> urls, CancellationToken ct)
        {
            var ordered = new List<KeyValuePair<string, bool>>();
            if (urls.Count == 0) return ordered;
            if (urls.Count == 1)
            {
                var single = await ProbeOneAsync(client, urls[0], ct).ConfigureAwait(false);
                ordered.Add(new KeyValuePair<string, bool>(urls[0], single.Ok && single.SupportsRange));
                return ordered;
            }

            var tasks = new List<Task<ProbeResult>>();
            foreach (string u in urls) tasks.Add(ProbeOneAsync(client, u, ct));

            var winners = new List<ProbeResult>();
            Task firstDone = await Task.WhenAny(tasks).ConfigureAwait(false);
            // Brief grace window so other fast mirrors can also finish.
            await Task.WhenAny(Task.WhenAll(tasks), Task.Delay(650, ct)).ConfigureAwait(false);

            foreach (var t in tasks)
            {
                if (t.IsCompleted && !t.IsFaulted && !t.IsCanceled && t.Result.Ok)
                    winners.Add(t.Result);
            }
            winners.Sort(delegate (ProbeResult a, ProbeResult b) { return a.ElapsedMs.CompareTo(b.ElapsedMs); });

            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var w in winners)
            {
                if (seen.Add(w.Url)) ordered.Add(new KeyValuePair<string, bool>(w.Url, w.SupportsRange));
            }
            foreach (string u in urls)
            {
                if (seen.Add(u)) ordered.Add(new KeyValuePair<string, bool>(u, false));
            }
            return ordered;
        }

        private static async Task<ProbeResult> ProbeOneAsync(HttpClient client, string url, CancellationToken ct)
        {
            var sw = Stopwatch.StartNew();
            try
            {
                using (var req = new HttpRequestMessage(HttpMethod.Get, url))
                {
                    req.Headers.Range = new RangeHeaderValue(0, 0);
                    using (var cts = CancellationTokenSource.CreateLinkedTokenSource(ct))
                    {
                        cts.CancelAfter(TimeSpan.FromSeconds(6));
                        using (HttpResponseMessage resp = await client.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, cts.Token).ConfigureAwait(false))
                        {
                            bool ok = resp.StatusCode == HttpStatusCode.PartialContent || resp.StatusCode == HttpStatusCode.OK;
                            bool ranges = resp.StatusCode == HttpStatusCode.PartialContent
                                || (resp.Headers.AcceptRanges != null && resp.Headers.AcceptRanges.Contains("bytes"));
                            return new ProbeResult { Url = url, Ok = ok, SupportsRange = ranges, ElapsedMs = sw.Elapsed.TotalMilliseconds };
                        }
                    }
                }
            }
            catch (Exception)
            {
                return new ProbeResult { Url = url, Ok = false, SupportsRange = false, ElapsedMs = double.MaxValue };
            }
        }

        public static async Task ExecuteInstallAsync(
            HttpClient client,
            PortableVariantPlan plan,
            InstallOptions opts,
            Action<InstallProgress> report,
            CancellationToken ct)
        {
            string targetDir = Path.GetFullPath((opts.TargetDir ?? "").Trim());
            if (string.IsNullOrEmpty(targetDir))
                throw new ArgumentException(I18n.T("err_invalid_dir"));

            string parentDir = Path.GetDirectoryName(targetDir);
            if (string.IsNullOrEmpty(parentDir))
                throw new ArgumentException(I18n.T("err_invalid_dir"));
            Directory.CreateDirectory(parentDir);

            // Preflight disk space check (need >= 2.2x archive size + 256 MiB headroom)
            long freeBytes = WindowsIntegration.GetAvailableFreeSpace(parentDir);
            long needBytes = (long)(plan.TotalBytes * 2.2) + (256L * 1024L * 1024L);
            if (freeBytes > 0 && freeBytes < needBytes)
            {
                throw new IOException(I18n.F("err_disk_space",
                    ReleaseCatalog.FormatBytes(freeBytes), ReleaseCatalog.FormatBytes(needBytes)));
            }

            string cacheRoot = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "VWA-Installer-Cache",
                plan.Version + "-" + plan.Variant);
            Directory.CreateDirectory(cacheRoot);

            bool isLocal = !string.IsNullOrEmpty(opts.ManifestBaseOverride);
            var sw = Stopwatch.StartNew();
            long speedSampleBytes = 0;
            double speedSampleSec = 0;
            double currentSpeed = 0;
            string activeRoute = "";

            Action<int, long, long, string> emit = delegate (int stage, long done, long total, string msg)
            {
                double nowSec = sw.Elapsed.TotalSeconds;
                if (stage == 1 && nowSec - speedSampleSec >= 0.45)
                {
                    long delta = done - speedSampleBytes;
                    if (delta >= 0) currentSpeed = delta / (nowSec - speedSampleSec);
                    speedSampleBytes = done;
                    speedSampleSec = nowSec;
                }
                else if (stage != 1)
                {
                    currentSpeed = 0;
                }
                double pct = total > 0 ? Math.Min(100.0, (done * 100.0) / total) : 0.0;
                report(new InstallProgress
                {
                    Stage = stage,
                    DoneBytes = done,
                    TotalBytes = total,
                    Percent = pct,
                    SpeedBps = currentSpeed,
                    ActiveRoute = activeRoute,
                    Workers = Math.Max(1, opts.Workers),
                    Message = msg
                });
            };

            // Stage 1: Download all parts (parallel Range chunks + persistent resume)
            var partPaths = new List<string>();
            long completedBefore = 0;
            for (int i = 0; i < plan.Parts.Count; i++)
            {
                ct.ThrowIfCancellationRequested();
                AssetPart part = plan.Parts[i];
                string destPath = Path.Combine(cacheRoot, part.Name);
                int partIdx = i + 1;
                int partCount = plan.Parts.Count;

                emit(1, completedBefore, plan.TotalBytes, I18n.T("status_probing"));
                var candidates = ReleaseCatalog.CandidateUrls(part.Url, opts.RouteSource, isLocal);
                var probed = await ProbeUrlsFastestFirstAsync(client, candidates, ct).ConfigureAwait(false);
                var orderedUrls = new List<string>();
                foreach (var kv in probed) orderedUrls.Add(kv.Key);
                bool supportsRange = probed.Count > 0 && probed[0].Value;
                activeRoute = orderedUrls.Count > 0 ? ReleaseCatalog.RouteLabel(orderedUrls[0]) : "GitHub";

                long baseOffset = completedBefore;
                Action<long> onPartBytes = delegate (long partDone)
                {
                    emit(1, Math.Min(plan.TotalBytes, baseOffset + partDone), plan.TotalBytes,
                        I18n.F("status_downloading", partIdx, partCount, part.Name));
                };

                await DownloadOnePartAsync(
                    client, part, destPath, orderedUrls, supportsRange, opts, onPartBytes, ct).ConfigureAwait(false);

                partPaths.Add(destPath);
                completedBefore += part.Bytes;
            }

            // Stage 2: Merge split parts (if > 1) and verify full archive SHA-256
            string archivePath;
            if (partPaths.Count == 1 && string.Equals(Path.GetFileName(partPaths[0]), plan.ArchiveName, StringComparison.OrdinalIgnoreCase))
            {
                archivePath = partPaths[0];
                emit(2, plan.TotalBytes, plan.TotalBytes, I18n.T("status_verifying"));
            }
            else
            {
                archivePath = Path.Combine(cacheRoot, plan.ArchiveName);
                await MergePartsVerifiedAsync(partPaths, archivePath, plan,
                    delegate (long done) { emit(2, done, plan.TotalBytes, I18n.F("status_merging", partPaths.Count)); },
                    ct).ConfigureAwait(false);
            }

            // Stage 3: Safe Zip64 extraction into staging dir + non-destructive upgrade preservation
            string stagingDir = Path.Combine(parentDir, "." + Path.GetFileName(targetDir) + ".staging-" + Process.GetCurrentProcess().Id);
            if (Directory.Exists(stagingDir)) Directory.Delete(stagingDir, true);
            Directory.CreateDirectory(stagingDir);

            try
            {
                ExtractZipVerified(archivePath, stagingDir, plan.Version,
                    delegate (int done, int total) { emit(3, done, total, I18n.F("status_extracting", done, total)); },
                    ct);

                // Stage 4: Preserve existing user data if upgrading an existing folder, write initial config & shortcuts
                emit(4, 25, 100, I18n.T("status_migrating"));
                ApplyStagingToTarget(stagingDir, targetDir, opts, ct);

                emit(4, 70, 100, I18n.T("status_shortcuts"));
                WindowsIntegration.WriteInitialConfigIfNeeded(targetDir, I18n.CurrentLanguage, opts.RouteSource, opts.ProxyMode, opts.ProxyUrl);
                if (opts.CreateStartMenu || opts.CreateDesktop)
                {
                    WindowsIntegration.CreateShortcutsAndUninstall(
                        targetDir, plan.Version, opts.CreateStartMenu, opts.CreateDesktop);
                }

                // Clean up temporary merged archive (keep nothing giant in %LOCALAPPDATA% cache after success)
                try { Directory.Delete(cacheRoot, true); } catch (Exception) { }

                emit(4, 100, 100, I18n.F("status_done", plan.Version));
            }
            catch (Exception)
            {
                try { if (Directory.Exists(stagingDir)) Directory.Delete(stagingDir, true); } catch (Exception) { }
                throw;
            }
        }

        private static async Task DownloadOnePartAsync(
            HttpClient client,
            AssetPart part,
            string destPath,
            List<string> orderedUrls,
            bool supportsRange,
            InstallOptions opts,
            Action<long> onProgress,
            CancellationToken ct)
        {
            if (File.Exists(destPath) && new FileInfo(destPath).Length == part.Bytes)
            {
                if (string.Equals(ComputeSha256(destPath, ct), part.Sha256, StringComparison.OrdinalIgnoreCase))
                {
                    onProgress(part.Bytes);
                    return;
                }
                File.Delete(destPath);
            }

            string partialPath = destPath + ".part";
            string statePath = destPath + ".part.state.json";
            bool usedChunked = false;

            if (supportsRange && opts.Workers > 1 && part.Bytes >= opts.ParallelMinBytes)
            {
                try
                {
                    await DownloadChunkedAsync(client, orderedUrls, partialPath, statePath, part.Bytes, part.Sha256,
                        opts.Workers, opts.ChunkBytes, onProgress, ct).ConfigureAwait(false);
                    usedChunked = true;
                }
                catch (OperationCanceledException) { throw; }
                catch (Exception)
                {
                    try { if (File.Exists(statePath)) File.Delete(statePath); } catch (Exception) { }
                }
            }

            if (!usedChunked)
            {
                await DownloadStreamingAsync(client, orderedUrls, partialPath, part.Bytes, onProgress, ct).ConfigureAwait(false);
            }

            string actualSha = ComputeSha256(partialPath, ct);
            if (!string.Equals(actualSha, part.Sha256, StringComparison.OrdinalIgnoreCase))
            {
                try { File.Delete(partialPath); } catch (Exception) { }
                try { if (File.Exists(statePath)) File.Delete(statePath); } catch (Exception) { }
                throw new InvalidDataException("SHA-256 verification failed for " + part.Name);
            }
            try { if (File.Exists(statePath)) File.Delete(statePath); } catch (Exception) { }
            if (File.Exists(destPath)) File.Delete(destPath);
            File.Move(partialPath, destPath);
        }

        public static async Task DownloadChunkedAsync(
            HttpClient client,
            List<string> urls,
            string partialPath,
            string statePath,
            long expectedBytes,
            string expectedSha,
            int workers,
            int chunkBytes,
            Action<long> onProgress,
            CancellationToken ct)
        {
            int cSize = Math.Max(4096, chunkBytes);
            var chunks = new List<Tuple<int, long, long>>();
            int idx = 0;
            for (long s = 0; s < expectedBytes; s += cSize)
            {
                long e = Math.Min(expectedBytes - 1, s + cSize - 1);
                chunks.Add(Tuple.Create(idx++, s, e));
            }

            var serializer = new JavaScriptSerializer();
            var completed = new HashSet<int>();
            if (File.Exists(statePath) && File.Exists(partialPath) && new FileInfo(partialPath).Length == expectedBytes)
            {
                try
                {
                    var saved = serializer.DeserializeObject(File.ReadAllText(statePath, Encoding.UTF8)) as Dictionary<string, object>;
                    if (saved != null
                        && ReleaseCatalog.GetLong(saved, "bytes") == expectedBytes
                        && string.Equals(ReleaseCatalog.GetString(saved, "sha256"), expectedSha, StringComparison.OrdinalIgnoreCase))
                    {
                        object arr;
                        if (saved.TryGetValue("completed", out arr) && arr is IEnumerable)
                        {
                            foreach (object item in (IEnumerable)arr)
                            {
                                int ci = Convert.ToInt32(item, CultureInfo.InvariantCulture);
                                if (ci >= 0 && ci < chunks.Count) completed.Add(ci);
                            }
                        }
                    }
                }
                catch (Exception) { completed.Clear(); }
            }

            if (!File.Exists(partialPath) || new FileInfo(partialPath).Length != expectedBytes)
            {
                using (var fs = new FileStream(partialPath, FileMode.Create, FileAccess.Write, FileShare.None))
                    fs.SetLength(expectedBytes);
                completed.Clear();
            }

            long doneBytes = 0;
            foreach (var c in chunks)
                if (completed.Contains(c.Item1)) doneBytes += (c.Item3 - c.Item2 + 1);
            onProgress(doneBytes);

            object ioLock = new object();
            var pending = new Queue<Tuple<int, long, long>>();
            foreach (var c in chunks)
                if (!completed.Contains(c.Item1)) pending.Enqueue(c);

            if (pending.Count == 0) return;

            int workerCount = Math.Max(1, Math.Min(workers, pending.Count));
            var tasks = new List<Task>();

            for (int w = 0; w < workerCount; w++)
            {
                tasks.Add(Task.Run(async delegate
                {
                    while (true)
                    {
                        Tuple<int, long, long> job;
                        lock (ioLock)
                        {
                            if (pending.Count == 0) return;
                            job = pending.Dequeue();
                        }
                        int cIdx = job.Item1;
                        long start = job.Item2;
                        long end = job.Item3;
                        int want = (int)(end - start + 1);

                        Exception lastErr = null;
                        bool ok = false;
                        for (int attempt = 0; attempt < 4; attempt++)
                        {
                            ct.ThrowIfCancellationRequested();
                            string url = urls[attempt % urls.Count];
                            long addedThisAttempt = 0;
                            try
                            {
                                using (var req = new HttpRequestMessage(HttpMethod.Get, url))
                                {
                                    req.Headers.Range = new RangeHeaderValue(start, end);
                                    using (var resp = await client.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct).ConfigureAwait(false))
                                    {
                                        if (resp.StatusCode != HttpStatusCode.PartialContent)
                                            throw new IOException("Server did not return HTTP 206 for Range request.");
                                        byte[] buf = new byte[want];
                                        int offset = 0;
                                        using (Stream stream = await resp.Content.ReadAsStreamAsync().ConfigureAwait(false))
                                        {
                                            while (offset < want)
                                            {
                                                ct.ThrowIfCancellationRequested();
                                                int n = await stream.ReadAsync(buf, offset, Math.Min(256 * 1024, want - offset), ct).ConfigureAwait(false);
                                                if (n <= 0) break;
                                                offset += n;
                                                addedThisAttempt += n;
                                                long snapshot = Interlocked.Add(ref doneBytes, n);
                                                onProgress(Math.Min(expectedBytes, snapshot));
                                            }
                                        }
                                        if (offset != want)
                                            throw new IOException("Truncated chunk " + cIdx + " (" + offset + " != " + want + ")");

                                        lock (ioLock)
                                        {
                                            using (var outFs = new FileStream(partialPath, FileMode.Open, FileAccess.Write, FileShare.ReadWrite))
                                            {
                                                outFs.Seek(start, SeekOrigin.Begin);
                                                outFs.Write(buf, 0, want);
                                            }
                                            completed.Add(cIdx);
                                            var doneList = new List<int>(completed);
                                            doneList.Sort();
                                            string tmpState = statePath + ".tmp";
                                            File.WriteAllText(tmpState, serializer.Serialize(new Dictionary<string, object>
                                            {
                                                { "bytes", expectedBytes },
                                                { "sha256", expectedSha },
                                                { "completed", doneList }
                                            }), new UTF8Encoding(false));
                                            if (File.Exists(statePath)) File.Delete(statePath);
                                            File.Move(tmpState, statePath);
                                        }
                                        ok = true;
                                        break;
                                    }
                                }
                            }
                            catch (OperationCanceledException) { throw; }
                            catch (Exception ex)
                            {
                                if (addedThisAttempt > 0) Interlocked.Add(ref doneBytes, -addedThisAttempt);
                                lastErr = ex;
                            }
                            if (!ok)
                                await Task.Delay(Math.Min(2500, 300 * (1 << attempt)), ct).ConfigureAwait(false);
                        }
                        if (!ok) throw new IOException("Chunk " + cIdx + " failed: " + (lastErr != null ? lastErr.Message : "unknown"), lastErr);
                    }
                }, ct));
            }
            await Task.WhenAll(tasks).ConfigureAwait(false);
        }

        private static async Task DownloadStreamingAsync(
            HttpClient client, List<string> urls, string partialPath, long expectedBytes, Action<long> onProgress, CancellationToken ct)
        {
            Exception lastErr = null;
            foreach (string url in urls)
            {
                ct.ThrowIfCancellationRequested();
                long existing = File.Exists(partialPath) ? new FileInfo(partialPath).Length : 0;
                if (existing > expectedBytes) { File.Delete(partialPath); existing = 0; }
                try
                {
                    using (var req = new HttpRequestMessage(HttpMethod.Get, url))
                    {
                        if (existing > 0) req.Headers.Range = new RangeHeaderValue(existing, null);
                        using (var resp = await client.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct).ConfigureAwait(false))
                        {
                            resp.EnsureSuccessStatusCode();
                            if (existing > 0 && resp.StatusCode != HttpStatusCode.PartialContent) existing = 0;
                            long written = existing;
                            onProgress(written);
                            FileMode mode = existing > 0 ? FileMode.Append : FileMode.Create;
                            using (var stream = await resp.Content.ReadAsStreamAsync().ConfigureAwait(false))
                            using (var outFs = new FileStream(partialPath, mode, FileAccess.Write, FileShare.None, 256 * 1024))
                            {
                                byte[] buf = new byte[256 * 1024];
                                int n;
                                while ((n = await stream.ReadAsync(buf, 0, buf.Length, ct).ConfigureAwait(false)) > 0)
                                {
                                    ct.ThrowIfCancellationRequested();
                                    outFs.Write(buf, 0, n);
                                    written += n;
                                    onProgress(written);
                                }
                            }
                        }
                    }
                    if (new FileInfo(partialPath).Length != expectedBytes)
                        throw new IOException("File size mismatch after download.");
                    return;
                }
                catch (OperationCanceledException) { throw; }
                catch (Exception ex) { lastErr = ex; }
            }
            throw new IOException("All download mirrors failed: " + (lastErr != null ? lastErr.Message : "unknown"), lastErr);
        }

        private static async Task MergePartsVerifiedAsync(
            List<string> partPaths, string mergedPath, PortableVariantPlan plan, Action<long> onProgress, CancellationToken ct)
        {
            if (File.Exists(mergedPath) && new FileInfo(mergedPath).Length == plan.TotalBytes)
            {
                if (string.Equals(ComputeSha256(mergedPath, ct), plan.ArchiveSha256, StringComparison.OrdinalIgnoreCase))
                {
                    onProgress(plan.TotalBytes);
                    return;
                }
                File.Delete(mergedPath);
            }

            string tmp = mergedPath + ".merging";
            try
            {
                await Task.Run(delegate
                {
                    long written = 0;
                    using (var sha = SHA256.Create())
                    using (var outFs = new FileStream(tmp, FileMode.Create, FileAccess.Write, FileShare.None, 2 * 1024 * 1024))
                    {
                        byte[] buf = new byte[2 * 1024 * 1024];
                        foreach (string p in partPaths)
                        {
                            using (var inFs = new FileStream(p, FileMode.Open, FileAccess.Read, FileShare.Read, 2 * 1024 * 1024))
                            {
                                int n;
                                while ((n = inFs.Read(buf, 0, buf.Length)) > 0)
                                {
                                    ct.ThrowIfCancellationRequested();
                                    outFs.Write(buf, 0, n);
                                    sha.TransformBlock(buf, 0, n, null, 0);
                                    written += n;
                                    onProgress(written);
                                }
                            }
                        }
                        sha.TransformFinalBlock(new byte[0], 0, 0);
                        string actual = BytesToHex(sha.Hash);
                        if (written != plan.TotalBytes || !string.Equals(actual, plan.ArchiveSha256, StringComparison.OrdinalIgnoreCase))
                            throw new InvalidDataException("Merged archive SHA-256 verification failed.");
                    }
                    if (File.Exists(mergedPath)) File.Delete(mergedPath);
                    File.Move(tmp, mergedPath);
                }, ct).ConfigureAwait(false);
            }
            finally
            {
                try { if (File.Exists(tmp)) File.Delete(tmp); } catch (Exception) { }
            }
        }

        public static void ExtractZipVerified(
            string zipPath, string stagingDir, string expectedVersion, Action<int, int> onEntry, CancellationToken ct)
        {
            string stagingFull = Path.GetFullPath(stagingDir).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            using (ZipArchive archive = ZipFile.OpenRead(zipPath))
            {
                int total = Math.Max(1, archive.Entries.Count);
                var topRoots = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                foreach (ZipArchiveEntry entry in archive.Entries)
                {
                    string norm = ValidateZipEntryName(entry.FullName);
                    string[] segs = norm.Split(new[] { '/' }, StringSplitOptions.RemoveEmptyEntries);
                    if (segs.Length > 0) topRoots.Add(segs[0]);
                }
                string stripRoot = null;
                if (topRoots.Count == 1)
                {
                    foreach (string r in topRoots)
                    {
                        if (string.Equals(r, "VWA", StringComparison.OrdinalIgnoreCase)
                            || string.Equals(r, "BVWA", StringComparison.OrdinalIgnoreCase))
                            stripRoot = r;
                    }
                }

                int idx = 0;
                foreach (ZipArchiveEntry entry in archive.Entries)
                {
                    ct.ThrowIfCancellationRequested();
                    idx++;
                    string norm = ValidateZipEntryName(entry.FullName);
                    string[] segs = norm.Split(new[] { '/' }, StringSplitOptions.RemoveEmptyEntries);
                    int startIdx = (stripRoot != null && segs.Length > 0 && string.Equals(segs[0], stripRoot, StringComparison.OrdinalIgnoreCase)) ? 1 : 0;
                    if (segs.Length <= startIdx) continue;

                    string relWin = string.Join(Path.DirectorySeparatorChar.ToString(), segs, startIdx, segs.Length - startIdx);
                    string dest = Path.GetFullPath(Path.Combine(stagingDir, relWin));
                    if (!dest.StartsWith(stagingFull, StringComparison.OrdinalIgnoreCase))
                        throw new InvalidDataException("Archive path escapes staging directory: " + entry.FullName);

                    if (entry.FullName.EndsWith("/", StringComparison.Ordinal))
                    {
                        Directory.CreateDirectory(dest);
                    }
                    else
                    {
                        string dir = Path.GetDirectoryName(dest);
                        if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
                        using (Stream src = entry.Open())
                        using (var dst = new FileStream(dest, FileMode.Create, FileAccess.Write, FileShare.None, 512 * 1024))
                            src.CopyTo(dst, 512 * 1024);
                    }
                    if (idx % 20 == 0 || idx == total) onEntry(idx, total);
                }
            }

            string verPath = Path.Combine(stagingDir, "version.json");
            if (!File.Exists(verPath))
                throw new InvalidDataException("Extracted archive is missing version.json.");
            if (!string.IsNullOrEmpty(expectedVersion))
            {
                var ser = new JavaScriptSerializer();
                string raw = File.ReadAllText(verPath, Encoding.UTF8).TrimStart('\uFEFF');
                var doc = ser.DeserializeObject(raw) as Dictionary<string, object>;
                string got = ReleaseCatalog.GetString(doc, "version").TrimStart('v', 'V');
                string want = expectedVersion.TrimStart('v', 'V');
                if (!string.Equals(got, want, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("Extracted version mismatch: expected " + want + ", got " + got);
            }
        }

        private static string ValidateZipEntryName(string name)
        {
            if (string.IsNullOrEmpty(name) || name.Contains("\\") || name.StartsWith("/") || name.Contains(":"))
                throw new InvalidDataException("Illegal archive entry path: " + name);
            string[] parts = name.Split('/');
            foreach (string part in parts)
                if (part == "..") throw new InvalidDataException("Path traversal entry rejected: " + name);
            return name;
        }

        private static void ApplyStagingToTarget(string stagingDir, string targetDir, InstallOptions opts, CancellationToken ct)
        {
            bool existing = Directory.Exists(targetDir) && Directory.GetFileSystemEntries(targetDir).Length > 0;
            if (!existing)
            {
                if (Directory.Exists(targetDir)) Directory.Delete(targetDir, false);
                Directory.Move(stagingDir, targetDir);
                return;
            }

            // Non-destructive in-place upgrade: copy user data from existing targetDir into stagingDir, then swap cleanly.
            MigrateExistingUserData(targetDir, stagingDir, ct);
            string backupDir = targetDir + ".old-" + Process.GetCurrentProcess().Id;
            if (Directory.Exists(backupDir)) Directory.Delete(backupDir, true);
            Directory.Move(targetDir, backupDir);
            try
            {
                Directory.Move(stagingDir, targetDir);
            }
            catch (Exception)
            {
                try { if (!Directory.Exists(targetDir) && Directory.Exists(backupDir)) Directory.Move(backupDir, targetDir); }
                catch (Exception) { }
                throw;
            }
            try { Directory.Delete(backupDir, true); } catch (Exception) { }
        }

        private static void MigrateExistingUserData(string oldRoot, string newRoot, CancellationToken ct)
        {
            string oldCfgPath = Path.Combine(oldRoot, "config.json");
            string newCfgPath = Path.Combine(newRoot, "config.json");
            string newExamplePath = Path.Combine(newRoot, "config.example.json");
            if (File.Exists(oldCfgPath))
            {
                try
                {
                    var ser = new JavaScriptSerializer { MaxJsonLength = 4 * 1024 * 1024 };
                    var oldCfg = ser.DeserializeObject(File.ReadAllText(oldCfgPath, Encoding.UTF8).TrimStart('\uFEFF')) as Dictionary<string, object>;
                    string baseSrc = File.Exists(newExamplePath) ? newExamplePath : (File.Exists(newCfgPath) ? newCfgPath : null);
                    var merged = baseSrc != null
                        ? (ser.DeserializeObject(File.ReadAllText(baseSrc, Encoding.UTF8).TrimStart('\uFEFF')) as Dictionary<string, object> ?? new Dictionary<string, object>())
                        : new Dictionary<string, object>();
                    if (oldCfg != null)
                    {
                        foreach (var kv in oldCfg)
                        {
                            if (!InternalConfigKeys.Contains(kv.Key)) merged[kv.Key] = kv.Value;
                        }
                        File.WriteAllText(newCfgPath, ser.Serialize(merged), new UTF8Encoding(false));
                    }
                }
                catch (Exception) { }
            }

            foreach (string rel in PreservedDirs)
            {
                string src = Path.Combine(oldRoot, rel);
                if (Directory.Exists(src)) CopyTreeMerge(src, Path.Combine(newRoot, rel), ct);
            }

            foreach (string rel in QwenWeightFiles)
            {
                string src = Path.Combine(oldRoot, rel);
                string dst = Path.Combine(newRoot, rel);
                if (File.Exists(src) && !File.Exists(dst))
                {
                    string d = Path.GetDirectoryName(dst);
                    if (!string.IsNullOrEmpty(d)) Directory.CreateDirectory(d);
                    File.Copy(src, dst, false);
                }
            }
        }

        private static void CopyTreeMerge(string srcDir, string dstDir, CancellationToken ct)
        {
            Directory.CreateDirectory(dstDir);
            foreach (string file in Directory.GetFiles(srcDir))
            {
                ct.ThrowIfCancellationRequested();
                string dst = Path.Combine(dstDir, Path.GetFileName(file));
                if (!File.Exists(dst)) File.Copy(file, dst, false);
            }
            foreach (string sub in Directory.GetDirectories(srcDir))
            {
                CopyTreeMerge(sub, Path.Combine(dstDir, Path.GetFileName(sub)), ct);
            }
        }
    }
}
