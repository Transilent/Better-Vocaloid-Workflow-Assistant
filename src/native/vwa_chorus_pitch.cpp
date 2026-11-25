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
        stretch.setFormantFactor(1.0f, preserve_formants != 0);

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
