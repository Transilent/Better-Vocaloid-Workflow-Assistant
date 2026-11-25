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
    if (rate < 8000 || rate > 96000 || channels < 1 || channels > 16 || frames <= 0
        || pcm == nullptr || shift_semitones == nullptr || output == nullptr) {
        return -1;
    }
    try {
        signalsmith::stretch::SignalsmithStretch<float> stretch(42L);
        stretch.presetDefault(channels, float(rate));
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
            // Avoid cepstral presence-band dulling (-0.9 dB @ 2.5-5 kHz) on small
            // everyday shifts; smoothly engage formant protection above 1.25 st.
            if (preserve_formants != 0 && std::abs(semi) > 1.25) {
                const double alpha = std::clamp((std::abs(semi) - 1.25) / 1.25, 0.0, 1.0);
                stretch.setFormantSemitones(float(-semi * alpha), false);
            } else {
                stretch.setFormantFactor(1.0f, false);
            }
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
        stretch.presetDefault(channels, float(rate));
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
    if (rate < 8000 || rate > 96000 || channels < 1 || channels > 16
        || in_frames <= 0 || out_frames <= 0 || pad_frames < 0 || pad_frames >= in_frames
        || pcm == nullptr || source_map == nullptr || output == nullptr) {
        return -1;
    }
    try {
        signalsmith::stretch::SignalsmithStretch<float> stretch(42L);
        stretch.presetDefault(channels, float(rate));
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
            if (shift_semitones != nullptr) {
                const int o_mid = u + out_n / 2 + out_lat - y0;
                if (o_mid >= 0 && o_mid < out_frames) {
                    semi = shift_semitones[o_mid];
                    if (!std::isfinite(semi)) {
                        return -2;
                    }
                    semi = std::clamp(semi, -72.0, 72.0);
                }
            }
            stretch.setTransposeSemitones(float(semi), tonality_limit);
            if (preserve_formants != 0 && std::abs(semi) > 1.25) {
                const double alpha = std::clamp((std::abs(semi) - 1.25) / 1.25, 0.0, 1.0);
                stretch.setFormantSemitones(float(-semi * alpha), false);
            } else {
                stretch.setFormantFactor(1.0f, false);
            }

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
