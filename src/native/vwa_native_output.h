#ifndef VWA_NATIVE_OUTPUT_H
#define VWA_NATIVE_OUTPUT_H

#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32) && defined(VWA_OUTPUT_BUILD_DLL)
#define VWA_OUTPUT_API __declspec(dllexport)
#elif defined(_WIN32)
#define VWA_OUTPUT_API
#else
#define VWA_OUTPUT_API
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define VWA_OUTPUT_ABI_VERSION 1u

enum {
    VWA_OUTPUT_BACKEND_NULL = 0,
    VWA_OUTPUT_BACKEND_WASAPI = 1
};

enum {
    VWA_OUTPUT_SHARE_NONE = 0,
    VWA_OUTPUT_SHARE_SHARED = 1
};

enum {
    VWA_OUTPUT_OK = 0,
    VWA_OUTPUT_ERR_INVALID_ARGUMENT = -1,
    VWA_OUTPUT_ERR_CLOSED = -2,
    VWA_OUTPUT_ERR_STALE_EPOCH = -3,
    VWA_OUTPUT_ERR_WRONG_BACKEND = -4,
    VWA_OUTPUT_ERR_DEVICE_INIT = -5,
    VWA_OUTPUT_ERR_DEVICE_START = -6,
    VWA_OUTPUT_ERR_DEVICE_STOP = -7,
    VWA_OUTPUT_ERR_ALLOCATION = -8
};

typedef struct VwaOutput VwaOutput;

/*
 * All fields are fixed-width integers so the ctypes mirror stays stable.
 * media_frame counts source frames consumed by the native consumer. It is a
 * callback-submission position, not an audible position. submitted_frames also
 * includes paused and underrun zero fill.
 */
typedef struct {
    uint64_t abi_version;
    uint64_t backend;
    uint64_t share_mode;
    uint64_t epoch;
    uint64_t capacity_frames;
    uint64_t queued_frames;
    uint64_t media_frame;
    uint64_t accepted_end_frame;
    uint64_t submitted_frames;
    uint64_t sample_rate;
    uint64_t channels;
    uint64_t period_frames;
    uint64_t requested_period_frames;
    uint64_t actual_device_sample_rate;
    uint64_t actual_device_period_frames;
    uint64_t device_buffer_frames;
    uint64_t callback_frames_last;
    uint64_t callback_frames_min;
    uint64_t callback_frames_max;
    uint64_t callback_count;
    uint64_t underrun_frames;
    uint64_t xrun_count;
    uint64_t pause_frames;
    uint64_t stale_epoch_writes;
    uint64_t continuity_rejections;
    uint64_t partial_writes;
    uint64_t seek_count;
    uint64_t seek_padding_before;
    uint64_t seek_padding_after_stop;
    uint64_t seek_padding_after_restart;
    uint64_t seek_padding_evidence;
    uint64_t seek_clock_position_before;
    uint64_t seek_clock_frequency_before;
    uint64_t seek_clock_timestamp_before;
    uint64_t seek_clock_position_after_stop;
    uint64_t seek_clock_frequency_after_stop;
    uint64_t seek_clock_timestamp_after_stop;
    uint64_t seek_clock_evidence;
    uint64_t seek_clock_reset_zero_ok;
    uint64_t wasapi_padding_frames;
    uint64_t wasapi_padding_query_ok;
    uint64_t wasapi_clock_position;
    uint64_t wasapi_clock_frequency;
    uint64_t wasapi_clock_timestamp;
    uint64_t reserved_clock_characteristics;
    uint64_t wasapi_clock_query_ok;
    uint64_t reserved_clock_characteristics_query_ok;
    uint64_t wasapi_default_device_period_100ns;
    uint64_t wasapi_minimum_device_period_100ns;
    uint64_t wasapi_stream_latency_100ns;
    uint64_t playing;
    uint64_t closing;
    uint64_t ring_owner_violations;
    int64_t device_error_code;
    int64_t error_code;
    char device_error[256];
    char clock_error[256];
    char last_error[256];
} VwaOutputSnapshot;

VWA_OUTPUT_API const char* vwa_output_abi_string(void);
VWA_OUTPUT_API uint64_t vwa_output_snapshot_size(void);

VWA_OUTPUT_API int vwa_output_create(
    uint32_t mode,
    uint32_t sample_rate,
    uint32_t channels,
    uint32_t capacity_frames,
    uint32_t period_frames,
    VwaOutput** out_output,
    char* error_text,
    size_t error_text_capacity);

VWA_OUTPUT_API void vwa_output_destroy(VwaOutput* output);

VWA_OUTPUT_API uint64_t vwa_output_write(
    VwaOutput* output,
    const float* samples,
    uint64_t frames,
    uint64_t epoch,
    uint64_t start_frame);

VWA_OUTPUT_API int vwa_output_seek(
    VwaOutput* output,
    uint64_t frame,
    uint64_t epoch,
    char* error_text,
    size_t error_text_capacity);

VWA_OUTPUT_API int vwa_output_set_playing(
    VwaOutput* output,
    uint32_t playing,
    char* error_text,
    size_t error_text_capacity);

VWA_OUTPUT_API int vwa_output_snapshot(
    VwaOutput* output,
    VwaOutputSnapshot* snapshot,
    char* error_text,
    size_t error_text_capacity);

VWA_OUTPUT_API uint64_t vwa_output_pull(
    VwaOutput* output,
    float* samples,
    uint64_t frames);

#ifdef __cplusplus
}
#endif

#endif
