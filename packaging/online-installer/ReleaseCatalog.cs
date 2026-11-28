using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;

namespace VwaOnlineInstaller
{
    internal sealed class AssetPart
    {
        public string Name;
        public long Bytes;
        public string Sha256;
        public string Url;
    }

    internal sealed class PortableVariantPlan
    {
        public string Version;
        public string Variant;
        public string ArchiveName;
        public long TotalBytes;
        public string ArchiveSha256;
        public List<AssetPart> Parts = new List<AssetPart>();
    }

    internal sealed class ReleaseInfo
    {
        public string Version;
        public string ReleaseUrl;
        public string Notes;
        public PortableVariantPlan FullPlan;
        public PortableVariantPlan JpnPlan;
    }

    internal static class ReleaseCatalog
    {
        public const string Repo = "Transilent/Better-Vocaloid-Workflow-Assistant";
        public const string ReleasesBase = "https://github.com/" + Repo + "/releases";
        public const string ApiLatest = "https://api.github.com/repos/" + Repo + "/releases/latest";
        public const string UserAgent = "VWA-OnlineInstaller/3.0";

        private static readonly Regex Sha256Regex = new Regex("^[a-f0-9]{64}$", RegexOptions.Compiled);

        public static readonly Dictionary<string, string[]> MirrorPrefixes = new Dictionary<string, string[]>
        {
            { "auto", new[] { "", "https://api.gitproxy.dev/", "https://gh-proxy.com/", "https://ghfast.top/" } },
            { "mirror", new[] { "https://api.gitproxy.dev/", "https://gh-proxy.com/", "https://ghfast.top/", "" } },
            { "direct", new[] { "" } },
            { "gitproxy", new[] { "https://api.gitproxy.dev/", "https://gh-proxy.com/", "https://ghfast.top/", "" } },
            { "gh-proxy", new[] { "https://gh-proxy.com/", "https://api.gitproxy.dev/", "https://ghfast.top/", "" } },
            { "ghfast", new[] { "https://ghfast.top/", "https://api.gitproxy.dev/", "https://gh-proxy.com/", "" } }
        };

        public static string AssetUrl(string version, string filename, string manifestBaseOverride)
        {
            if (!string.IsNullOrEmpty(manifestBaseOverride))
                return manifestBaseOverride.TrimEnd('/') + "/" + Uri.EscapeDataString(filename);
            string tag = version.StartsWith("v", StringComparison.OrdinalIgnoreCase) ? version : ("v" + version);
            return ReleasesBase + "/download/" + tag + "/" + Uri.EscapeDataString(filename);
        }

        public static List<string> CandidateUrls(string url, string source, bool isLocalOverride)
        {
            var list = new List<string>();
            if (isLocalOverride || !url.StartsWith("https://github.com/", StringComparison.OrdinalIgnoreCase))
            {
                list.Add(url);
                return list;
            }
            string[] prefixes;
            if (!MirrorPrefixes.TryGetValue(source ?? "auto", out prefixes))
                prefixes = MirrorPrefixes["auto"];
            foreach (string prefix in prefixes)
            {
                string full = string.IsNullOrEmpty(prefix) ? url : (prefix + url);
                if (!list.Contains(full)) list.Add(full);
            }
            return list;
        }

        public static string RouteLabel(string url)
        {
            string lower = (url ?? "").ToLowerInvariant();
            if (lower.Contains("gitproxy")) return "GitProxy";
            if (lower.Contains("gh-proxy")) return "GH-Proxy";
            if (lower.Contains("ghfast")) return "ghfast.top";
            if (lower.Contains("127.0.0.1") || lower.Contains("localhost")) return "Local";
            return "GitHub";
        }

        public static string FormatBytes(long bytes)
        {
            if (bytes <= 0) return "0 B";
            string[] units = { "B", "KB", "MB", "GB", "TB" };
            double val = bytes;
            int idx = 0;
            while (val >= 1024.0 && idx < units.Length - 1) { val /= 1024.0; idx++; }
            return string.Format(CultureInfo.InvariantCulture, idx >= 2 ? "{0:0.00} {1}" : "{0:0} {1}", val, units[idx]);
        }

        public static async Task<ReleaseInfo> FetchReleaseAsync(
            HttpClient client, string pinnedVersion, string routeSource, string manifestBaseOverride, CancellationToken ct)
        {
            bool local = !string.IsNullOrEmpty(manifestBaseOverride);
            string version = (pinnedVersion ?? "").Trim().TrimStart('v', 'V');
            Dictionary<string, object> releaseAssets = null;
            string notes = "";

            string invUrl = local
                ? (manifestBaseOverride.TrimEnd('/') + "/release-assets.json")
                : (string.IsNullOrEmpty(version)
                    ? (ReleasesBase + "/latest/download/release-assets.json")
                    : AssetUrl(version, "release-assets.json", null));

            try
            {
                releaseAssets = await FetchJsonAsync(client, invUrl, routeSource, local, ct).ConfigureAwait(false);
                if (releaseAssets != null)
                {
                    if (string.IsNullOrEmpty(version)) version = GetString(releaseAssets, "version").TrimStart('v', 'V');
                    notes = GetString(releaseAssets, "release_notes");
                }
            }
            catch (Exception)
            {
                if (local && string.IsNullOrEmpty(version)) throw;
            }

            if (string.IsNullOrEmpty(version) && !local)
            {
                var apiDoc = await FetchJsonAsync(client, ApiLatest, "direct", false, ct).ConfigureAwait(false);
                version = GetString(apiDoc, "tag_name").TrimStart('v', 'V');
                if (string.IsNullOrEmpty(notes)) notes = GetString(apiDoc, "body");
                if (releaseAssets == null && !string.IsNullOrEmpty(version))
                {
                    try { releaseAssets = await FetchJsonAsync(client, AssetUrl(version, "release-assets.json", null), routeSource, false, ct).ConfigureAwait(false); }
                    catch (Exception) { }
                }
            }

            if (string.IsNullOrEmpty(version))
                throw new InvalidOperationException("Unable to determine latest VWA release version.");

            var info = new ReleaseInfo
            {
                Version = version,
                ReleaseUrl = ReleasesBase + "/tag/v" + version,
                Notes = notes
            };

            Task<PortableVariantPlan> fullTask = TryFetchVariantAsync(client, version, "full", routeSource, manifestBaseOverride, releaseAssets, ct);
            Task<PortableVariantPlan> jpnTask = TryFetchVariantAsync(client, version, "jpn", routeSource, manifestBaseOverride, releaseAssets, ct);
            await Task.WhenAll(fullTask, jpnTask).ConfigureAwait(false);
            info.FullPlan = fullTask.Result;
            info.JpnPlan = jpnTask.Result;

            if (info.FullPlan == null && info.JpnPlan == null)
                throw new InvalidOperationException("No valid Full or JPN portable metadata found for v" + version + ".");

            return info;
        }

        private static async Task<PortableVariantPlan> TryFetchVariantAsync(
            HttpClient client, string version, string variant, string routeSource,
            string manifestBaseOverride, Dictionary<string, object> releaseAssets, CancellationToken ct)
        {
            bool local = !string.IsNullOrEmpty(manifestBaseOverride);
            string metaName = "portable-metadata-" + variant + ".json";
            Dictionary<string, object> meta = null;
            try
            {
                meta = await FetchJsonAsync(client, AssetUrl(version, metaName, manifestBaseOverride), routeSource, local, ct).ConfigureAwait(false);
            }
            catch (Exception)
            {
                meta = ExtractVariantFromInventory(releaseAssets, variant);
            }
            if (meta == null) return null;
            try { return ParseVariantPlan(meta, version, variant, manifestBaseOverride); }
            catch (Exception) { return null; }
        }

        private static Dictionary<string, object> ExtractVariantFromInventory(Dictionary<string, object> inv, string variant)
        {
            if (inv == null) return null;
            object rawVariants;
            if ((inv.TryGetValue("portable_variants", out rawVariants) || inv.TryGetValue("variants", out rawVariants))
                && rawVariants is Dictionary<string, object>)
            {
                object picked;
                if (((Dictionary<string, object>)rawVariants).TryGetValue(variant, out picked) && picked is Dictionary<string, object>)
                    return (Dictionary<string, object>)picked;
            }
            if (variant == "full" && inv.ContainsKey("archive"))
                return inv;
            return null;
        }

        public static PortableVariantPlan ParseVariantPlan(
            Dictionary<string, object> meta, string version, string variant, string manifestBaseOverride)
        {
            object rawArch;
            if (!meta.TryGetValue("archive", out rawArch) || !(rawArch is Dictionary<string, object>))
                throw new InvalidDataException("Missing archive metadata.");
            var arch = (Dictionary<string, object>)rawArch;
            string archName = GetString(arch, "name");
            long totalBytes = GetLong(arch, "bytes");
            string archSha = GetString(arch, "sha256").ToLowerInvariant();
            if (!archName.EndsWith(".zip", StringComparison.OrdinalIgnoreCase) || totalBytes <= 0 || !Sha256Regex.IsMatch(archSha))
                throw new InvalidDataException("Invalid archive metadata.");

            var plan = new PortableVariantPlan
            {
                Version = version,
                Variant = variant,
                ArchiveName = archName,
                TotalBytes = totalBytes,
                ArchiveSha256 = archSha
            };

            object rawParts;
            if (meta.TryGetValue("parts", out rawParts) && rawParts is IEnumerable && !(rawParts is string))
            {
                long sum = 0;
                foreach (object item in (IEnumerable)rawParts)
                {
                    var d = item as Dictionary<string, object>;
                    if (d == null) continue;
                    string pName = GetString(d, "name");
                    long pBytes = GetLong(d, "bytes");
                    string pSha = GetString(d, "sha256").ToLowerInvariant();
                    if (string.IsNullOrEmpty(pName) || pName.Contains("/") || pName.Contains("\\") || pName.Contains("..")
                        || pBytes <= 0 || !Sha256Regex.IsMatch(pSha))
                        throw new InvalidDataException("Invalid part metadata: " + pName);
                    sum += pBytes;
                    plan.Parts.Add(new AssetPart
                    {
                        Name = pName,
                        Bytes = pBytes,
                        Sha256 = pSha,
                        Url = AssetUrl(version, pName, manifestBaseOverride)
                    });
                }
                if (plan.Parts.Count > 0 && sum != totalBytes)
                    throw new InvalidDataException("Part byte sum does not match archive total bytes.");
            }

            if (plan.Parts.Count == 0)
            {
                plan.Parts.Add(new AssetPart
                {
                    Name = archName,
                    Bytes = totalBytes,
                    Sha256 = archSha,
                    Url = AssetUrl(version, archName, manifestBaseOverride)
                });
            }
            return plan;
        }

        private static async Task<Dictionary<string, object>> FetchJsonAsync(
            HttpClient client, string url, string routeSource, bool isLocalOverride, CancellationToken ct)
        {
            Exception lastErr = null;
            var serializer = new JavaScriptSerializer { MaxJsonLength = 8 * 1024 * 1024 };
            foreach (string target in CandidateUrls(url, routeSource, isLocalOverride))
            {
                ct.ThrowIfCancellationRequested();
                try
                {
                    using (var req = new HttpRequestMessage(HttpMethod.Get, target))
                    {
                        req.Headers.TryAddWithoutValidation("User-Agent", UserAgent);
                        using (var cts = CancellationTokenSource.CreateLinkedTokenSource(ct))
                        {
                            cts.CancelAfter(TimeSpan.FromSeconds(18));
                            using (HttpResponseMessage resp = await client.SendAsync(req, HttpCompletionOption.ResponseContentRead, cts.Token).ConfigureAwait(false))
                            {
                                resp.EnsureSuccessStatusCode();
                                string text = await resp.Content.ReadAsStringAsync().ConfigureAwait(false);
                                if (text.Length > 0 && text[0] == '\uFEFF') text = text.Substring(1);
                                var parsed = serializer.DeserializeObject(text) as Dictionary<string, object>;
                                if (parsed != null) return parsed;
                            }
                        }
                    }
                }
                catch (Exception ex)
                {
                    if (ct.IsCancellationRequested) throw;
                    lastErr = ex;
                }
            }
            throw new IOException("Failed to fetch " + url + ": " + (lastErr != null ? lastErr.Message : "unknown error"), lastErr);
        }

        public static string GetString(Dictionary<string, object> dict, string key)
        {
            object v;
            if (dict != null && dict.TryGetValue(key, out v) && v != null)
                return Convert.ToString(v, CultureInfo.InvariantCulture) ?? "";
            return "";
        }

        public static long GetLong(Dictionary<string, object> dict, string key)
        {
            object v;
            if (dict == null || !dict.TryGetValue(key, out v) || v == null) return 0;
            try { return Convert.ToInt64(v, CultureInfo.InvariantCulture); }
            catch (Exception) { return 0; }
        }
    }
}
