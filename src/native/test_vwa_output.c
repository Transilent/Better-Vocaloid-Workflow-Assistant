#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <inttypes.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "vwa_native_output.h"

static float sample_value(uint64_t frame, uint32_t channel)
{
    uint64_t value = (frame * UINT64_C(17) + (uint64_t)channel * UINT64_C(29)) % UINT64_C(1009);
    return ((float)value / 1008.0f) - 0.5f;
}

static void fill_pattern(float* samples, uint64_t frames, uint32_t channels, uint64_t start_frame)
{
    uint64_t frame;
    uint32_t channel;
    for (frame = 0u; frame < frames; ++frame) {
        for (channel = 0u; channel < channels; ++channel) {
            samples[frame * channels + channel] = sample_value(start_frame + frame, channel);
        }
    }
}

static int compare_pattern(
    const float* samples,
    uint64_t frames,
    uint32_t channels,
    uint64_t start_frame)
{
    uint64_t frame;
    uint32_t channel;
    for (frame = 0u; frame < frames; ++frame) {
        for (channel = 0u; channel < channels; ++channel) {
            float expected = sample_value(start_frame + frame, channel);
            if (fabsf(samples[frame * channels + channel] - expected) > 1.0e-7f) {
                return 0;
            }
        }
    }
    return 1;
}

static int all_zero(const float* samples, uint64_t frames, uint32_t channels)
{
    uint64_t index;
    for (index = 0u; index < frames * channels; ++index) {
        if (samples[index] != 0.0f) {
            return 0;
        }
    }
    return 1;
}

static int snapshot_of(VwaOutput* output, VwaOutputSnapshot* snapshot)
{
    char error[256] = {0};
    int result = vwa_output_snapshot(output, snapshot, error, sizeof(error));
    if (result != VWA_OUTPUT_OK) {
        fprintf(stderr, "snapshot failed (%d): %s\n", result, error);
        return 0;
    }
    return 1;
}

static int run_null_test(void)
{
    VwaOutput* output = NULL;
    VwaOutputSnapshot before;
    VwaOutputSnapshot snapshot;
    char error[256] = {0};
    float input[1200];
    float rendered[640];
    uint64_t accepted;
    uint64_t pulled;
    uint64_t media_before_pause;
    int failures = 0;
    int result;

#define CHECK(condition, message) do { \
    if (!(condition)) { \
        fprintf(stderr, "NULL FAIL: %s\n", (message)); \
        failures += 1; \
    } \
} while (0)

    result = vwa_output_create(VWA_OUTPUT_BACKEND_NULL, 48000u, 2u, 256u, 64u, &output, error, sizeof(error));
    CHECK(result == VWA_OUTPUT_OK, error);
    if (result != VWA_OUTPUT_OK) {
        return failures;
    }
    CHECK(snapshot_of(output, &before) != 0, "initial snapshot");
    CHECK(before.backend == VWA_OUTPUT_BACKEND_NULL, "null backend");
    CHECK(before.share_mode == VWA_OUTPUT_SHARE_NONE, "null share mode");
    CHECK(before.epoch == 0u, "initial epoch must be zero");
    CHECK(before.capacity_frames == 256u, "capacity");
    CHECK(before.queued_frames == 0u, "initial queue");
    CHECK(before.media_frame == 0u, "initial media frame");

    result = vwa_output_seek(output, 0u, 1u, error, sizeof(error));
    CHECK(result == VWA_OUTPUT_OK, error);
    result = vwa_output_set_playing(output, 1u, error, sizeof(error));
    CHECK(result == VWA_OUTPUT_OK, error);

    fill_pattern(input, 100u, 2u, 0u);
    accepted = vwa_output_write(output, input, 100u, 1u, 0u);
    CHECK(accepted == 100u, "initial full write");
    CHECK(snapshot_of(output, &snapshot) != 0, "snapshot after write");
    CHECK(snapshot.epoch == 1u, "seek registered epoch one");
    CHECK(snapshot.queued_frames == 100u, "queued frames after write");
    CHECK(snapshot.media_frame == 0u, "write must not advance media frame");
    CHECK(snapshot.accepted_end_frame == 100u, "accepted tail");

    memset(rendered, 0, sizeof(rendered));
    pulled = vwa_output_pull(output, rendered, 64u);
    CHECK(pulled == 64u, "pull fixed block");
    CHECK(compare_pattern(rendered, 64u, 2u, 0u), "pulled sample determinism");
    CHECK(snapshot_of(output, &snapshot) != 0, "snapshot after first pull");
    CHECK(snapshot.media_frame == 64u, "media frame after first pull");
    CHECK(snapshot.queued_frames == 36u, "queue after first pull");

    fill_pattern(input, 500u, 2u, 100u);
    accepted = vwa_output_write(output, input, 500u, 1u, 100u);
    CHECK(accepted == 220u, "partial write accepts available capacity");
    CHECK(snapshot_of(output, &snapshot) != 0, "snapshot after partial write");
    CHECK(snapshot.queued_frames == 256u, "ring full after partial write");
    CHECK(snapshot.accepted_end_frame == 320u, "accepted tail advances by accepted frames only");
    CHECK(snapshot.partial_writes == 1u, "partial write counter");

    memset(rendered, 0, sizeof(rendered));
    pulled = vwa_output_pull(output, rendered, 300u);
    CHECK(pulled == 300u, "null pull always returns requested frame count");
    CHECK(compare_pattern(rendered, 256u, 2u, 64u), "wrapped ring samples");
    CHECK(all_zero(rendered + 256u * 2u, 44u, 2u), "underrun suffix must be zero");
    CHECK(snapshot_of(output, &snapshot) != 0, "snapshot after underrun");
    CHECK(snapshot.media_frame == 320u, "media frame advances only by valid frames");
    CHECK(snapshot.underrun_frames == 44u, "underrun frames");
    CHECK(snapshot.xrun_count == 1u, "xrun count");
    CHECK(snapshot.queued_frames == 0u, "empty after underrun pull");

    fill_pattern(input, 1u, 2u, 600u);
    accepted = vwa_output_write(output, input, 1u, 0u, 600u);
    CHECK(accepted == 0u, "stale epoch write must be rejected");
    accepted = vwa_output_write(output, input, 1u, 1u, 321u);
    CHECK(accepted == 0u, "non-contiguous start_frame must be rejected");
    CHECK(snapshot_of(output, &snapshot) != 0, "snapshot after rejected writes");
    CHECK(snapshot.stale_epoch_writes == 1u, "stale epoch rejection counter");
    CHECK(snapshot.continuity_rejections == 1u, "continuity rejection counter");

    media_before_pause = snapshot.media_frame;
    result = vwa_output_set_playing(output, 0u, error, sizeof(error));
    CHECK(result == VWA_OUTPUT_OK, error);
    memset(rendered, 0x7f, sizeof(rendered));
    pulled = vwa_output_pull(output, rendered, 64u);
    CHECK(pulled == 64u, "paused pull");
    CHECK(all_zero(rendered, 64u, 2u), "pause emits zero");
    CHECK(snapshot_of(output, &snapshot) != 0, "snapshot paused");
    CHECK(snapshot.media_frame == media_before_pause, "pause must not advance media_frame");
    CHECK(snapshot.pause_frames == 64u, "pause frame counter");

    result = vwa_output_seek(output, 48000u, 2u, error, sizeof(error));
    CHECK(result == VWA_OUTPUT_OK, error);
    CHECK(snapshot_of(output, &snapshot) != 0, "snapshot after seek");
    CHECK(snapshot.epoch == 2u, "seek epoch");
    CHECK(snapshot.media_frame == 48000u, "seek target media frame");
    CHECK(snapshot.accepted_end_frame == 48000u, "seek target accepted tail");
    CHECK(snapshot.queued_frames == 0u, "seek cleared ring");
    CHECK(snapshot.seek_count == 2u, "seek count");

    result = vwa_output_set_playing(output, 1u, error, sizeof(error));
    CHECK(result == VWA_OUTPUT_OK, error);
    fill_pattern(input, 64u, 2u, 48000u);
    accepted = vwa_output_write(output, input, 64u, 2u, 48000u);
    CHECK(accepted == 64u, "write after seek");
    {
        const uint64_t block_sizes[] = { 3u, 5u, 17u, 39u };
        uint64_t consumed = 0u;
        size_t block_index;
        for (block_index = 0u; block_index < sizeof(block_sizes) / sizeof(block_sizes[0]); ++block_index) {
            memset(rendered, 0, sizeof(rendered));
            pulled = vwa_output_pull(output, rendered, block_sizes[block_index]);
            CHECK(pulled == block_sizes[block_index], "variable null pull size");
            CHECK(compare_pattern(rendered, block_sizes[block_index], 2u, 48000u + consumed),
                  "variable null pull samples");
            consumed += block_sizes[block_index];
        }
        CHECK(consumed == 64u, "variable block sum");
    }
    CHECK(snapshot_of(output, &snapshot) != 0, "final snapshot");
    CHECK(snapshot.media_frame == 48064u, "final media frame");
    CHECK(snapshot.ring_owner_violations == 0u, "ring owner violations");

    vwa_output_destroy(output);
#undef CHECK
    printf(
        "{\"schema\":\"vwa.native_output.null_test.v1\",\"passed\":%s,"
        "\"failures\":%d,\"initial_epoch\":0,\"final_epoch\":%" PRIu64 ","
        "\"partial_accepted\":220,\"underrun_frames\":44,"
        "\"stale_epoch_writes\":1,\"continuity_rejections\":1,"
        "\"pause_advances_media\":false,\"deterministic_samples\":true,"
        "\"ring_owner_violations\":0}\n",
        failures == 0 ? "true" : "false",
        failures,
        snapshot.epoch);
    return failures;
}

static int run_wasapi_silent_probe(void)
{
    VwaOutput* output = NULL;
    VwaOutputSnapshot snapshot;
    char error[256] = {0};
    int result;
    int failures = 0;
    int waited_ms = 0;
    int device_available = 1;
    int callbacks_observed = 0;
    int clock_available = 0;
    int clock_reset_zero = 0;

    result = vwa_output_create(VWA_OUTPUT_BACKEND_WASAPI, 48000u, 2u, 4096u, 256u, &output, error, sizeof(error));
    if (result != VWA_OUTPUT_OK) {
        device_available = 0;
        printf(
            "{\"schema\":\"vwa.native_output.wasapi_silent_probe.v1\","
            "\"passed\":false,\"result\":\"blocked_device_unavailable\","
            "\"played_nonzero_audio\":false,\"exclusive_mode\":false,"
            "\"error\":\"%s\",\"error_code\":%d}\n",
            error,
            result);
        return 2;
    }

    while (waited_ms < 3000) {
        if (!snapshot_of(output, &snapshot)) {
            failures += 1;
            break;
        }
        if (snapshot.callback_count > 0u) {
            callbacks_observed = 1;
            break;
        }
        Sleep(10u);
        waited_ms += 10;
    }

    if (!callbacks_observed) {
        fprintf(stderr, "WASAPI FAIL: no silent callback observed within 3 seconds\n");
        failures += 1;
    }

    if (snapshot.share_mode != VWA_OUTPUT_SHARE_SHARED) {
        failures += 1;
    }
    if (snapshot.playing != 0u) {
        failures += 1;
    }
    if (snapshot.actual_device_sample_rate == 0u || snapshot.actual_device_period_frames == 0u ||
        snapshot.device_buffer_frames == 0u) {
        failures += 1;
    }
    if (snapshot.pause_frames == 0u) {
        failures += 1;
    }

    result = vwa_output_seek(output, 0u, 1u, error, sizeof(error));
    if (result != VWA_OUTPUT_OK) {
        fprintf(stderr, "WASAPI seek failed: %s\n", error);
        failures += 1;
    }
    if (!snapshot_of(output, &snapshot)) {
        failures += 1;
    }
    if (snapshot.epoch != 1u || snapshot.media_frame != 0u || snapshot.queued_frames != 0u ||
        snapshot.seek_count != 1u) {
        failures += 1;
    }
    clock_available = snapshot.seek_clock_evidence != 0u;
    if ((snapshot.seek_clock_evidence & UINT64_C(3)) == UINT64_C(3)) {
        clock_reset_zero = snapshot.seek_clock_reset_zero_ok != 0u;
        if (!clock_reset_zero) {
            failures += 1;
        }
    }

    printf(
        "{\"schema\":\"vwa.native_output.wasapi_silent_probe.v1\","
        "\"passed\":%s,\"result\":\"%s\",\"played_nonzero_audio\":false,"
        "\"exclusive_mode\":false,\"probe\":\"paused_zero_fill\","
        "\"device_available\":true,\"callbacks_observed\":%s,"
        "\"waited_ms\":%d,\"backend\":\"wasapi-shared\","
        "\"sample_rate\":%" PRIu64 ",\"channels\":%" PRIu64 ","
        "\"requested_period_frames\":%" PRIu64 ","
        "\"actual_device_sample_rate\":%" PRIu64 ","
        "\"actual_device_period_frames\":%" PRIu64 ","
        "\"device_buffer_frames\":%" PRIu64 ","
        "\"callback_frames_last\":%" PRIu64 ","
        "\"callback_frames_min\":%" PRIu64 ","
        "\"callback_frames_max\":%" PRIu64 ","
        "\"callback_count\":%" PRIu64 ","
        "\"pause_frames\":%" PRIu64 ","
        "\"wasapi_padding_query_ok\":%s,\"wasapi_padding_frames\":%" PRIu64 ","
        "\"seek_padding_evidence\":%" PRIu64 ","
        "\"seek_padding_before\":%" PRIu64 ","
        "\"seek_padding_after_stop\":%" PRIu64 ","
        "\"seek_padding_after_restart\":%" PRIu64 ","
        "\"clock_query_available\":%s,\"seek_clock_evidence\":%" PRIu64 ","
        "\"seek_clock_position_before\":%" PRIu64 ","
        "\"seek_clock_frequency_before\":%" PRIu64 ","
        "\"seek_clock_position_after_stop\":%" PRIu64 ","
        "\"seek_clock_frequency_after_stop\":%" PRIu64 ","
        "\"clock_reset_zero\":%s,\"device_error_code\":%" PRId64 ","
        "\"device_error\":\"%s\",\"clock_error\":\"%s\",\"failures\":%d}\n",
        failures == 0 ? "true" : "false",
        failures == 0 ? "passed_device_shared_silent" : "failed_device_checks",
        callbacks_observed ? "true" : "false",
        waited_ms,
        snapshot.sample_rate,
        snapshot.channels,
        snapshot.requested_period_frames,
        snapshot.actual_device_sample_rate,
        snapshot.actual_device_period_frames,
        snapshot.device_buffer_frames,
        snapshot.callback_frames_last,
        snapshot.callback_frames_min,
        snapshot.callback_frames_max,
        snapshot.callback_count,
        snapshot.pause_frames,
        snapshot.wasapi_padding_query_ok ? "true" : "false",
        snapshot.wasapi_padding_frames,
        snapshot.seek_padding_evidence,
        snapshot.seek_padding_before,
        snapshot.seek_padding_after_stop,
        snapshot.seek_padding_after_restart,
        clock_available ? "true" : "false",
        snapshot.seek_clock_evidence,
        snapshot.seek_clock_position_before,
        snapshot.seek_clock_frequency_before,
        snapshot.seek_clock_position_after_stop,
        snapshot.seek_clock_frequency_after_stop,
        clock_available ? (clock_reset_zero ? "true" : "false") : "null",
        snapshot.device_error_code,
        snapshot.device_error,
        snapshot.clock_error,
        failures);

    vwa_output_destroy(output);
    (void)device_available;
    return failures == 0 ? 0 : 3;
}

int main(int argc, char** argv)
{
    if (argc > 1 && strcmp(argv[1], "--wasapi-silent-json") == 0) {
        return run_wasapi_silent_probe();
    }
    if (argc > 1 && strcmp(argv[1], "--null-json") == 0) {
        return run_null_test();
    }
    fprintf(stderr, "usage: %s --null-json | --wasapi-silent-json\n", argv[0]);
    return 64;
}
