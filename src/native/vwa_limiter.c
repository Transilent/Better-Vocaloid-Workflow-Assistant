/*
 * Streaming state extracted from FFmpeg libavfilter/af_alimiter.c.
 *
 * Upstream source: https://raw.githubusercontent.com/FFmpeg/FFmpeg/n7.1/libavfilter/af_alimiter.c
 * Upstream SHA-256: dc15973088a1e6197ae1001cfecbc0d847c479571563c37b5e2efe4073fa56eb
 *
 * Copyright (C) 2001-2010 Krzysztof Foltman, Markus Schmidt,
 * Thor Harald Johansen and others
 * Copyright (c) 2015 Paul B Mahol
 *
 * This file is part of FFmpeg. Modifications for VWA streaming use, 2026.
 *
 * FFmpeg is free software; you can redistribute it and/or modify it under
 * the terms of the GNU Lesser General Public License as published by the
 * Free Software Foundation; either version 2.1 of the License, or (at your
 * option) any later version.
 *
 * FFmpeg is distributed in the hope that it will be useful, but WITHOUT ANY
 * WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
 * FOR A PARTICULAR PURPOSE. See the GNU Lesser General Public License for
 * more details.
 *
 * You should have received a copy of the GNU Lesser General Public License
 * along with FFmpeg; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
 */

#include "vwa_limiter.h"

#include <math.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define VWA_LIMITER_RELEASE_SECONDS (VWA_LIMITER_RELEASE_MS / 1000.0)
#define VWA_LIMITER_DRAIN_CHUNK_FRAMES UINT64_C(1024)

struct VwaLimiter {
    uint32_t sample_rate;
    uint32_t channels;
    uint32_t attack_frames;
    uint32_t buffer_size;
    uint32_t next_capacity;

    double* buffer;
    double* next_delta;
    int* next_pos;

    int pos;
    int next_iter;
    int next_len;
    double att;
    double delta;

    uint32_t trim_remaining;
    uint32_t drain_padding_remaining;
    uint64_t input_frames;
    uint64_t emitted_frames;
    bool draining;
    bool drained;
};

static void set_error(char* text, size_t capacity, const char* message)
{
    if (text == NULL || capacity == 0u) {
        return;
    }
    if (message == NULL) {
        message = "";
    }
    (void)snprintf(text, capacity, "%s", message);
}

static double ffmaxd(double a, double b)
{
    return a > b ? a : b;
}

static double ffmind(double a, double b)
{
    return a < b ? a : b;
}

static double ffclipd(double value, double minimum, double maximum)
{
    return ffmind(ffmaxd(value, minimum), maximum);
}


static void reset_state(VwaLimiter* limiter)
{
    memset(limiter->buffer, 0, (size_t)limiter->next_capacity * sizeof(*limiter->buffer));
    memset(limiter->next_delta, 0,
           (size_t)limiter->next_capacity * sizeof(*limiter->next_delta));
    memset(limiter->next_pos, 0xff,
           (size_t)limiter->next_capacity * sizeof(*limiter->next_pos));
    limiter->pos = 0;
    limiter->next_iter = 0;
    limiter->next_len = 0;
    limiter->att = 1.0;
    limiter->delta = 0.0;
    limiter->trim_remaining = limiter->attack_frames - 1u;
    limiter->drain_padding_remaining = limiter->attack_frames - 1u;
    limiter->input_frames = 0u;
    limiter->emitted_frames = 0u;
    limiter->draining = false;
    limiter->drained = false;
}

static bool input_is_finite(const float* samples, uint64_t sample_count)
{
    uint64_t index;
    for (index = 0u; index < sample_count; index++) {
        if (!isfinite((double)samples[index])) {
            return false;
        }
    }
    return true;
}

static uint64_t process_core(
    VwaLimiter* limiter,
    const float* input_samples,
    float* output_samples,
    uint64_t input_frames)
{
    const int channels = (int)limiter->channels;
    const int buffer_size = (int)limiter->buffer_size;
    const double limit = (double)VWA_LIMITER_LIMIT;
    const double sample_rate = (double)limiter->sample_rate;
    const double release = VWA_LIMITER_RELEASE_SECONDS;
    double* const buffer = limiter->buffer;
    double* const next_delta = limiter->next_delta;
    int* const next_pos = limiter->next_pos;
    const float* src = input_samples;
    float* dst = output_samples;
    uint64_t output_frames = 0u;
    uint64_t frame_index;

    for (frame_index = 0u; frame_index < input_frames; frame_index++) {
        double peak = 0.0;
        double output_values[2];
        double* delayed;
        int channel;

        for (channel = 0; channel < channels; channel++) {
            const double sample = (double)src[channel];
            buffer[limiter->pos + channel] = sample;
            peak = ffmaxd(peak, fabs(sample));
        }

        if (peak > limit) {
            const double patt = ffmind(limit / peak, 1.0);
            const double rdelta = (1.0 - patt) / (sample_rate * release);
            const double new_delta =
                (limit / peak - limiter->att) / buffer_size * channels;
            int found = 0;

            if (new_delta < limiter->delta) {
                limiter->delta = new_delta;
                next_pos[0] = limiter->pos;
                next_pos[1] = -1;
                next_delta[0] = rdelta;
                limiter->next_len = 1;
                limiter->next_iter = 0;
            } else {
                int i;
                for (i = limiter->next_iter;
                     i < limiter->next_iter + limiter->next_len;
                     i++) {
                    const int j = i % buffer_size;
                    double previous_peak = 0.0;
                    double previous_delta;

                    if (next_pos[j] >= 0) {
                        for (channel = 0; channel < channels; channel++) {
                            previous_peak = ffmaxd(
                                previous_peak,
                                fabs(buffer[next_pos[j] + channel]));
                        }
                    }
                    previous_delta =
                        (limit / peak - limit / previous_peak) /
                        (((buffer_size - next_pos[j] + limiter->pos) %
                          buffer_size) / channels);
                    if (previous_delta < next_delta[j]) {
                        next_delta[j] = previous_delta;
                        found = 1;
                        break;
                    }
                }
                if (found) {
                    limiter->next_len = i - limiter->next_iter + 1;
                    next_pos[(limiter->next_iter + limiter->next_len) %
                             buffer_size] = limiter->pos;
                    next_delta[(limiter->next_iter + limiter->next_len) %
                               buffer_size] = rdelta;
                    next_pos[(limiter->next_iter + limiter->next_len + 1) %
                             buffer_size] = -1;
                    limiter->next_len++;
                }
            }
        }

        delayed = &buffer[(limiter->pos + channels) % buffer_size];
        peak = 0.0;
        for (channel = 0; channel < channels; channel++) {
            peak = ffmaxd(peak, fabs(delayed[channel]));
        }

        limiter->att += limiter->delta;
        for (channel = 0; channel < channels; channel++) {
            output_values[channel] = delayed[channel] * limiter->att;
        }

        if ((limiter->pos + channels) % buffer_size ==
            next_pos[limiter->next_iter]) {
            limiter->delta = next_delta[limiter->next_iter];
            limiter->att = limit / peak;
            limiter->next_len--;
            next_pos[limiter->next_iter] = -1;
            limiter->next_iter = (limiter->next_iter + 1) % buffer_size;
        }

        if (limiter->att > 1.0) {
            limiter->att = 1.0;
            limiter->delta = 0.0;
            limiter->next_iter = 0;
            limiter->next_len = 0;
            next_pos[0] = -1;
        }

        if (limiter->att <= 0.0) {
            limiter->att = 0.0000000000001;
            limiter->delta = (1.0 - limiter->att) / (sample_rate * release);
        }

        if (limiter->att != 1.0 && (1.0 - limiter->att) < 0.0000000000001) {
            limiter->att = 1.0;
        }

        if (limiter->delta != 0.0 && fabs(limiter->delta) < 0.00000000000001) {
            limiter->delta = 0.0;
        }

        if (limiter->trim_remaining > 0u) {
            limiter->trim_remaining--;
        } else {
            for (channel = 0; channel < channels; channel++) {
                dst[channel] = (float)ffclipd(output_values[channel], -limit, limit);
            }
            dst += channels;
            output_frames++;
        }

        limiter->pos = (limiter->pos + channels) % buffer_size;
        src += channels;
    }

    return output_frames;
}

const char* vwa_limiter_abi_string(void)
{
    return "vwa-limiter/1 ffmpeg-n7.1-alimiter-lgpl2.1+";
}

uint32_t vwa_limiter_attack_frames(uint32_t sample_rate)
{
    const double attack_seconds = VWA_LIMITER_ATTACK_MS / 1000.0;
    if (sample_rate == 0u) {
        return 0u;
    }
    return (uint32_t)((double)sample_rate * attack_seconds);
}

int vwa_limiter_create(
    uint32_t sample_rate,
    uint32_t channels,
    VwaLimiter** out_limiter,
    char* error_text,
    size_t error_text_capacity)
{
    VwaLimiter* limiter;
    double attack_seconds;
    double raw_buffer_size;
    double raw_next_capacity;

    set_error(error_text, error_text_capacity, "");
    if (out_limiter == NULL) {
        set_error(error_text, error_text_capacity, "out_limiter must not be null");
        return VWA_LIMITER_ERR_INVALID_ARGUMENT;
    }
    *out_limiter = NULL;
    if (sample_rate == 0u || (channels != 1u && channels != 2u)) {
        set_error(error_text, error_text_capacity,
                  "sample_rate must be positive and channels must be mono or stereo");
        return VWA_LIMITER_ERR_INVALID_ARGUMENT;
    }

    attack_seconds = VWA_LIMITER_ATTACK_MS / 1000.0;
    raw_buffer_size = (double)sample_rate * attack_seconds * (double)channels;
    if (raw_buffer_size > (double)UINT32_MAX) {
        set_error(error_text, error_text_capacity, "limiter delay buffer is too large");
        return VWA_LIMITER_ERR_INVALID_ARGUMENT;
    }
    if (raw_buffer_size < (double)(channels + 1u)) {
        set_error(error_text, error_text_capacity, "5 ms attack is below one frame");
        return VWA_LIMITER_ERR_INVALID_ARGUMENT;
    }

    limiter = (VwaLimiter*)calloc(1u, sizeof(*limiter));
    if (limiter == NULL) {
        set_error(error_text, error_text_capacity, "cannot allocate limiter state");
        return VWA_LIMITER_ERR_ALLOCATION;
    }

    limiter->sample_rate = sample_rate;
    limiter->channels = channels;
    limiter->buffer_size = (uint32_t)raw_buffer_size;
    limiter->buffer_size -= limiter->buffer_size % channels;
    if (limiter->buffer_size < channels + 1u) {
        free(limiter);
        set_error(error_text, error_text_capacity, "5 ms attack is below one frame");
        return VWA_LIMITER_ERR_INVALID_ARGUMENT;
    }
    limiter->attack_frames = limiter->buffer_size / channels;

    raw_next_capacity =
        (double)sample_rate * (double)channels * 100.0 / 1000.0 + (double)channels;
    if (raw_next_capacity > (double)UINT32_MAX) {
        free(limiter);
        set_error(error_text, error_text_capacity,
                  "limiter attack tracking buffer is too large");
        return VWA_LIMITER_ERR_INVALID_ARGUMENT;
    }
    limiter->next_capacity = (uint32_t)raw_next_capacity;
    if (limiter->next_capacity < limiter->buffer_size + channels) {
        limiter->next_capacity = limiter->buffer_size + channels;
    }

    limiter->buffer = (double*)calloc(
        (size_t)limiter->next_capacity, sizeof(*limiter->buffer));
    limiter->next_delta = (double*)calloc(
        (size_t)limiter->next_capacity, sizeof(*limiter->next_delta));
    limiter->next_pos = (int*)malloc(
        (size_t)limiter->next_capacity * sizeof(*limiter->next_pos));
    if (limiter->buffer == NULL || limiter->next_delta == NULL ||
        limiter->next_pos == NULL) {
        vwa_limiter_destroy(limiter);
        set_error(error_text, error_text_capacity, "cannot allocate limiter buffers");
        return VWA_LIMITER_ERR_ALLOCATION;
    }

    reset_state(limiter);
    *out_limiter = limiter;
    return VWA_LIMITER_OK;
}

void vwa_limiter_destroy(VwaLimiter* limiter)
{
    if (limiter == NULL) {
        return;
    }
    free(limiter->buffer);
    free(limiter->next_delta);
    free(limiter->next_pos);
    free(limiter);
}

int vwa_limiter_process(
    VwaLimiter* limiter,
    const float* input_samples,
    uint64_t input_frames,
    float* output_samples,
    uint64_t output_capacity_frames,
    uint64_t* out_frames,
    char* error_text,
    size_t error_text_capacity)
{
    uint64_t emitted;

    set_error(error_text, error_text_capacity, "");
    if (out_frames == NULL) {
        set_error(error_text, error_text_capacity, "out_frames must not be null");
        return VWA_LIMITER_ERR_INVALID_ARGUMENT;
    }
    *out_frames = 0u;
    if (limiter == NULL || output_samples == NULL || output_capacity_frames < input_frames) {
        set_error(error_text, error_text_capacity,
                  "limiter, output_samples, or frame capacity is invalid");
        return VWA_LIMITER_ERR_INVALID_ARGUMENT;
    }
    if (input_frames > 0u && input_samples == NULL) {
        set_error(error_text, error_text_capacity, "input_samples must not be null");
        return VWA_LIMITER_ERR_INVALID_ARGUMENT;
    }
    if (limiter->draining || limiter->drained) {
        set_error(error_text, error_text_capacity, "limiter stream is finished; call reset");
        return VWA_LIMITER_ERR_FINISHED;
    }
    if (input_frames > 0u &&
        !input_is_finite(input_samples, input_frames * limiter->channels)) {
        set_error(error_text, error_text_capacity, "input contains NaN or infinity");
        return VWA_LIMITER_ERR_NONFINITE_INPUT;
    }
    if (input_frames == 0u) {
        return VWA_LIMITER_OK;
    }

    emitted = process_core(limiter, input_samples, output_samples, input_frames);
    limiter->input_frames += input_frames;
    limiter->emitted_frames += emitted;
    *out_frames = emitted;
    return VWA_LIMITER_OK;
}

int vwa_limiter_drain(
    VwaLimiter* limiter,
    float* output_samples,
    uint64_t output_capacity_frames,
    uint64_t* out_frames,
    char* error_text,
    size_t error_text_capacity)
{
    float zeros[VWA_LIMITER_DRAIN_CHUNK_FRAMES * 2u] = {0.0f};
    float* write;
    uint64_t remaining;
    uint64_t emitted_total = 0u;

    set_error(error_text, error_text_capacity, "");
    if (out_frames == NULL) {
        set_error(error_text, error_text_capacity, "out_frames must not be null");
        return VWA_LIMITER_ERR_INVALID_ARGUMENT;
    }
    *out_frames = 0u;
    if (limiter == NULL) {
        set_error(error_text, error_text_capacity, "limiter must not be null");
        return VWA_LIMITER_ERR_INVALID_ARGUMENT;
    }
    if (limiter->drained) {
        return VWA_LIMITER_OK;
    }
    if (limiter->draining) {
        set_error(error_text, error_text_capacity, "limiter drain is already in progress");
        return VWA_LIMITER_ERR_FINISHED;
    }
    if (limiter->emitted_frames > limiter->input_frames) {
        set_error(error_text, error_text_capacity, "limiter frame accounting is invalid");
        return VWA_LIMITER_ERR_INVALID_ARGUMENT;
    }

    remaining = limiter->input_frames - limiter->emitted_frames;
    if (output_capacity_frames < remaining ||
        (remaining > 0u && output_samples == NULL)) {
        set_error(error_text, error_text_capacity,
                  "drain output buffer is smaller than the remaining frame count");
        return VWA_LIMITER_ERR_BUFFER_TOO_SMALL;
    }

    limiter->draining = true;
    write = output_samples;
    while (limiter->drain_padding_remaining > 0u) {
        uint64_t chunk = limiter->drain_padding_remaining;
        uint64_t emitted;

        if (chunk > VWA_LIMITER_DRAIN_CHUNK_FRAMES) {
            chunk = VWA_LIMITER_DRAIN_CHUNK_FRAMES;
        }
        emitted = process_core(limiter, zeros, write, chunk);
        limiter->drain_padding_remaining -= (uint32_t)chunk;
        limiter->emitted_frames += emitted;
        emitted_total += emitted;
        if (emitted > 0u) {
            write += emitted * limiter->channels;
        }
    }
    limiter->draining = false;
    limiter->drained = true;
    *out_frames = emitted_total;

    if (emitted_total != remaining || limiter->emitted_frames != limiter->input_frames) {
        set_error(error_text, error_text_capacity,
                  "limiter drain frame accounting did not reach the input frame count");
        return VWA_LIMITER_ERR_INVALID_ARGUMENT;
    }
    return VWA_LIMITER_OK;
}

int vwa_limiter_reset(
    VwaLimiter* limiter,
    char* error_text,
    size_t error_text_capacity)
{
    set_error(error_text, error_text_capacity, "");
    if (limiter == NULL) {
        set_error(error_text, error_text_capacity, "limiter must not be null");
        return VWA_LIMITER_ERR_INVALID_ARGUMENT;
    }
    reset_state(limiter);
    return VWA_LIMITER_OK;
}