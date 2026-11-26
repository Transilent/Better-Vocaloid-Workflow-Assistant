// Native multichannel vocal pitch shifter backed by Signalsmith Stretch (MIT).
#include "signalsmith-stretch.h"

#include <algorithm>
#include <cmath>
#include <vector>

#if defined(_WIN32)
#define VWA_PITCH_EXPORT extern "C" __declspec(dllexport)
#else
#define VWA_PITCH_EXPORT extern "C" __attribute__((visibility("default")))
#endif

VWA_PITCH_EXPORT int vwa_chorus_pitch_abi() {
    return 1;
}

namespace {

static constexpr float kDefaultVocalWindowMs = 64.0f;

static void configure_vocal_stretch(
    signalsmith::stretch::SignalsmithStretch<float>& stretch,
    int channels,
    int rate,
    float window_ms
) {
    const float ms = (window_ms > 0.0f && std::isfinite(window_ms))
        ? std::clamp(window_ms, 32.0f, 160.0f)
        : kDefaultVocalWindowMs;
    const int block = std::max(256, static_cast<int>(std::lround(double(rate) * double(ms) * 0.001)));
    const int interval = std::max(64, block / 4);
    stretch.configure(channels, block, interval, false);
}

// Fast local autocorrelation F0 estimate (Hz) for vocal formant envelope tracking
// when no external RMVPE F0 curve is provided. Returns 0.0 for unvoiced frames.
static double estimate_local_f0_hz(
    const std::vector<std::vector<float>>& input,
    int channels,
    int center,
    int total_frames,
    int rate
) {
    const int dec = std::max(1, rate / 11025);
    const double eff_rate = double(rate) / double(dec);
    const int win = std::max(128, static_cast<int>(std::lround(eff_rate * 0.032)));
    const int min_lag = std::max(6, static_cast<int>(std::floor(eff_rate / 1100.0)));
    const int max_lag = std::min(win / 2, std::max(min_lag + 2, static_cast<int>(std::ceil(eff_rate / 72.0))));
    if (win <= max_lag + 4 || total_frames <= 0) {
        return 0.0;
    }
    const int half_src = (win * dec) / 2;
    const int start_src = center - half_src;
    std::vector<double> mono(win, 0.0);
    double mean = 0.0;
    const double inv_ch = 1.0 / double(std::max(1, channels));
    for (int i = 0; i < win; ++i) {
        const int idx = start_src + i * dec;
        if (idx >= 0 && idx < total_frames) {
            double s = 0.0;
            for (int ch = 0; ch < channels; ++ch) {
                s += double(input[ch][idx]);
            }
            mono[i] = s * inv_ch;
            mean += mono[i];
        }
    }
    mean /= double(win);
    double energy = 0.0;
    for (int i = 0; i < win; ++i) {
        mono[i] -= mean;
        energy += mono[i] * mono[i];
    }
    if (energy < 1e-7 * double(win)) {
        return 0.0;
    }
    int best_lag = 0;
    double best_r = 0.42;
    for (int lag = min_lag; lag <= max_lag; ++lag) {
        double corr = 0.0;
        double e1 = 0.0;
        double e2 = 0.0;
        const int n = win - lag;
        for (int i = 0; i < n; ++i) {
            const double a = mono[i];
            const double b = mono[i + lag];
            corr += a * b;
            e1 += a * a;
            e2 += b * b;
        }
        const double denom = std::sqrt(e1 * e2);
        if (denom > 1e-12) {
            const double r = corr / denom;
            if (r > best_r) {
                best_r = r;
                best_lag = lag;
            }
        }
    }
    if (best_lag <= 0) {
        return 0.0;
    }
    return eff_rate / double(best_lag);
}

static void apply_vocal_formants(
    signalsmith::stretch::SignalsmithStretch<float>& stretch,
    int rate,
    double semi,
    int preserve_formants,
    double f0_hz
) {
    if (preserve_formants != 0 && std::abs(semi) > 1.25) {
        if (std::isfinite(f0_hz) && f0_hz >= 55.0 && f0_hz <= 1600.0) {
            stretch.setFormantBase(float(f0_hz / double(rate)));
        } else {
            stretch.setFormantBase(0.0f);
        }
        const double alpha = std::clamp((std::abs(semi) - 1.25) / 1.25, 0.0, 1.0);
        stretch.setFormantSemitones(float(-semi * alpha), false);
    } else {
        stretch.setFormantBase(0.0f);
        stretch.setFormantFactor(1.0f, false);
    }
}

} // namespace

VWA_PITCH_EXPORT int vwa_chorus_pitch_stretch_vocal(
    int rate,
    int channels,
    int frames,
    const float* pcm,
    const double* shift_semitones,
    const double* f0_hz,
    int preserve_formants,
    float tonality_limit_hz,
    float window_ms,
    float* output
) {
    if (rate < 8000 || rate > 96000 || channels < 1 || channels > 16 || frames <= 0
        || pcm == nullptr || shift_semitones == nullptr || output == nullptr) {
        return -1;
    }
    try {
        signalsmith::stretch::SignalsmithStretch<float> stretch(42L);
        configure_vocal_stretch(stretch, channels, rate, window_ms);
        stretch.setFormantFactor(1.0f, false);

        const float tonality_limit = (tonality_limit_hz > 0.0f && std::isfinite(tonality_limit_hz))
            ? (tonality_limit_hz / float(rate))
            : 0.0f;
        const int analysis = stretch.inputLatency();
        const int latency = analysis + stretch.outputLatency();
        const int total = frames + latency;
        const int block = std::max(32, rate / 200);

        std::vector<std::vector<float>> input(channels, std::vector<float>(total, 0.0f));
        std::vector<std::vector<float>> rendered(channels, std::vector<float>(total, 0.0f));
        std::vector<const float*> src(channels);
        std::vector<float*> dst(channels);

        for (int ch = 0; ch < channels; ++ch) {
            std::copy_n(pcm + static_cast<size_t>(ch) * static_cast<size_t>(frames), frames, input[ch].data());
        }

        for (int start = 0; start < total; start += block) {
            const int count = std::min(block, total - start);
            const int center = std::clamp(start - analysis + count / 2, 0, frames - 1);
            double semi = shift_semitones[center];
            if (!std::isfinite(semi)) {
                return -2;
            }
            semi = std::clamp(semi, -72.0, 72.0);
            stretch.setTransposeSemitones(float(semi), tonality_limit);
            double local_f0 = 0.0;
            if (preserve_formants != 0 && std::abs(semi) > 1.25) {
                local_f0 = (f0_hz != nullptr)
                    ? f0_hz[center]
                    : estimate_local_f0_hz(input, channels, center, frames, rate);
            }
            apply_vocal_formants(stretch, rate, semi, preserve_formants, local_f0);
            for (int ch = 0; ch < channels; ++ch) {
                src[ch] = input[ch].data() + start;
                dst[ch] = rendered[ch].data() + start;
            }
            stretch.process(src, count, dst, count);
        }

        for (int ch = 0; ch < channels; ++ch) {
            std::copy_n(rendered[ch].data() + latency, frames,
                        output + static_cast<size_t>(ch) * static_cast<size_t>(frames));
        }
        return latency;
    } catch (...) {
        return -3;
    }
}

VWA_PITCH_EXPORT int vwa_chorus_pitch_stretch(
    int rate,
    int channels,
    int frames,
    const float* pcm,
    const double* shift_semitones,
    int preserve_formants,
    float tonality_limit_hz,
    float* output
) {
    return vwa_chorus_pitch_stretch_vocal(
        rate, channels, frames, pcm, shift_semitones, nullptr,
        preserve_formants, tonality_limit_hz, kDefaultVocalWindowMs, output
    );
}

VWA_PITCH_EXPORT int vwa_chorus_time_warp(
    int rate,
    int channels,
    int in_frames,
    int out_frames,
    int pad_frames,
    const float* pcm,
    const double* source_map,
    float* output
) {
    if (rate < 8000 || rate > 96000 || channels < 1 || channels > 16
        || in_frames <= 0 || out_frames <= 0 || pad_frames < 0 || pad_frames >= in_frames
        || pcm == nullptr || source_map == nullptr || output == nullptr) {
        return -1;
    }
    try {
        signalsmith::stretch::SignalsmithStretch<float> stretch(42L);
        configure_vocal_stretch(stretch, channels, rate, kDefaultVocalWindowMs);
        stretch.setTransposeFactor(1.0f, 0.0f);
        stretch.setFormantFactor(1.0f, false);

        const int in_lat = stretch.inputLatency();
        const int out_lat = stretch.outputLatency();
        const int block = std::max(64, stretch.intervalSamples() / 2);
        const int pre = std::max(pad_frames, in_lat + out_lat);
        const int left_zero = pre - pad_frames;
        const double map_end = source_map[out_frames];
        if (!std::isfinite(source_map[0]) || !std::isfinite(map_end) || map_end < 0.0) {
            return -2;
        }
        for (int i = 1; i <= out_frames; ++i) {
            if (!std::isfinite(source_map[i]) || source_map[i] < source_map[i - 1]) {
                return -2;
            }
        }

        const int y0 = pre + in_lat + out_lat;
        const int total_out = y0 + out_frames;
        const long long needed_in = static_cast<long long>(std::ceil(double(pre + in_lat) + map_end + double(out_lat))) + block + 16;
        const long long total_in = std::max<long long>(static_cast<long long>(left_zero) + in_frames, needed_in);

        std::vector<std::vector<float>> input(channels, std::vector<float>(static_cast<size_t>(total_in), 0.0f));
        std::vector<std::vector<float>> rendered(channels, std::vector<float>(static_cast<size_t>(total_out), 0.0f));
        std::vector<const float*> src(channels);
        std::vector<float*> dst(channels);

        for (int ch = 0; ch < channels; ++ch) {
            std::copy_n(pcm + static_cast<size_t>(ch) * static_cast<size_t>(in_frames),
                        in_frames, input[ch].data() + left_zero);
        }

        auto map_ext = [&](int o) -> double {
            if (o < 0) {
                return double(o);
            }
            if (o <= out_frames) {
                return source_map[o];
            }
            return map_end + double(o - out_frames);
        };

        long long fed = 0;
        for (int u = 0; u < total_out; u += block) {
            const int out_n = std::min(block, total_out - u);
            const double target_pos = double(pre + in_lat) + map_ext(u + out_n + out_lat - y0);
            long long target_x = std::llround(target_pos);
            if (target_x < fed) {
                target_x = fed;
            }
            if (target_x > total_in) {
                target_x = total_in;
            }
            const int in_n = static_cast<int>(target_x - fed);
            for (int ch = 0; ch < channels; ++ch) {
                src[ch] = input[ch].data() + fed;
                dst[ch] = rendered[ch].data() + u;
            }
            stretch.process(src, in_n, dst, out_n);
            fed = target_x;
        }

        for (int ch = 0; ch < channels; ++ch) {
            std::copy_n(rendered[ch].data() + y0, out_frames,
                        output + static_cast<size_t>(ch) * static_cast<size_t>(out_frames));
        }
        return in_lat + out_lat;
    } catch (...) {
        return -3;
    }
}

VWA_PITCH_EXPORT int vwa_chorus_pitch_time_warp_vocal(
    int rate,
    int channels,
    int in_frames,
    int out_frames,
    int pad_frames,
    const float* pcm,
    const double* source_map,
    const double* shift_semitones,
    const double* f0_hz,
    int preserve_formants,
    float tonality_limit_hz,
    float window_ms,
    float* output
) {
    if (rate < 8000 || rate > 96000 || channels < 1 || channels > 16
        || in_frames <= 0 || out_frames <= 0 || pad_frames < 0 || pad_frames >= in_frames
        || pcm == nullptr || source_map == nullptr || output == nullptr) {
        return -1;
    }
    try {
        signalsmith::stretch::SignalsmithStretch<float> stretch(42L);
        configure_vocal_stretch(stretch, channels, rate, window_ms);
        stretch.setTransposeFactor(1.0f, 0.0f);
        stretch.setFormantFactor(1.0f, false);

        const float tonality_limit = (tonality_limit_hz > 0.0f && std::isfinite(tonality_limit_hz))
            ? (tonality_limit_hz / float(rate))
            : 0.0f;
        const int in_lat = stretch.inputLatency();
        const int out_lat = stretch.outputLatency();
        const int block = std::max(64, stretch.intervalSamples() / 2);
        const int pre = std::max(pad_frames, in_lat + out_lat);
        const int left_zero = pre - pad_frames;
        const double map_end = source_map[out_frames];
        if (!std::isfinite(source_map[0]) || !std::isfinite(map_end) || map_end < 0.0) {
            return -2;
        }
        for (int i = 1; i <= out_frames; ++i) {
            if (!std::isfinite(source_map[i]) || source_map[i] < source_map[i - 1]) {
                return -2;
            }
        }

        const int y0 = pre + in_lat + out_lat;
        const int total_out = y0 + out_frames;
        const long long needed_in = static_cast<long long>(std::ceil(double(pre + in_lat) + map_end + double(out_lat))) + block + 16;
        const long long total_in = std::max<long long>(static_cast<long long>(left_zero) + in_frames, needed_in);

        std::vector<std::vector<float>> input(channels, std::vector<float>(static_cast<size_t>(total_in), 0.0f));
        std::vector<std::vector<float>> rendered(channels, std::vector<float>(static_cast<size_t>(total_out), 0.0f));
        std::vector<const float*> src(channels);
        std::vector<float*> dst(channels);

        for (int ch = 0; ch < channels; ++ch) {
            std::copy_n(pcm + static_cast<size_t>(ch) * static_cast<size_t>(in_frames),
                        in_frames, input[ch].data() + left_zero);
        }

        auto map_ext = [&](int o) -> double {
            if (o < 0) {
                return double(o);
            }
            if (o <= out_frames) {
                return source_map[o];
            }
            return map_end + double(o - out_frames);
        };

        long long fed = 0;
        for (int u = 0; u < total_out; u += block) {
            const int out_n = std::min(block, total_out - u);
            const double target_pos = double(pre + in_lat) + map_ext(u + out_n + out_lat - y0);
            long long target_x = std::llround(target_pos);
            if (target_x < fed) {
                target_x = fed;
            }
            if (target_x > total_in) {
                target_x = total_in;
            }
            const int in_n = static_cast<int>(target_x - fed);

            double semi = 0.0;
            double local_f0 = 0.0;
            const int o_mid = u + out_n / 2 + out_lat - y0;
            if (shift_semitones != nullptr && o_mid >= 0 && o_mid < out_frames) {
                semi = shift_semitones[o_mid];
                if (!std::isfinite(semi)) {
                    return -2;
                }
                semi = std::clamp(semi, -72.0, 72.0);
                if (preserve_formants != 0 && std::abs(semi) > 1.25) {
                    if (f0_hz != nullptr) {
                        local_f0 = f0_hz[o_mid];
                    } else {
                        const int src_center = static_cast<int>(std::clamp<long long>(
                            static_cast<long long>(std::llround(double(pre) + map_ext(o_mid))),
                            0LL, total_in - 1));
                        local_f0 = estimate_local_f0_hz(input, channels, src_center, static_cast<int>(total_in), rate);
                    }
                }
            }
            stretch.setTransposeSemitones(float(semi), tonality_limit);
            apply_vocal_formants(stretch, rate, semi, preserve_formants, local_f0);

            for (int ch = 0; ch < channels; ++ch) {
                src[ch] = input[ch].data() + fed;
                dst[ch] = rendered[ch].data() + u;
            }
            stretch.process(src, in_n, dst, out_n);
            fed = target_x;
        }

        for (int ch = 0; ch < channels; ++ch) {
            std::copy_n(rendered[ch].data() + y0, out_frames,
                        output + static_cast<size_t>(ch) * static_cast<size_t>(out_frames));
        }
        return in_lat + out_lat;
    } catch (...) {
        return -3;
    }
}

VWA_PITCH_EXPORT int vwa_chorus_pitch_time_warp(
    int rate,
    int channels,
    int in_frames,
    int out_frames,
    int pad_frames,
    const float* pcm,
    const double* source_map,
    const double* shift_semitones,
    int preserve_formants,
    float tonality_limit_hz,
    float* output
) {
    return vwa_chorus_pitch_time_warp_vocal(
        rate, channels, in_frames, out_frames, pad_frames, pcm, source_map,
        shift_semitones, nullptr, preserve_formants, tonality_limit_hz, kDefaultVocalWindowMs, output
    );
}
