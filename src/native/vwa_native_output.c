#ifndef _WIN32
#error "vwa_native_output.c currently implements the Windows/miniaudio WASAPI backend only."
#endif

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <initguid.h>
#include <audioclient.h>

#include <inttypes.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MINIAUDIO_IMPLEMENTATION
#define MA_ENABLE_ONLY_SPECIFIC_BACKENDS
#define MA_ENABLE_WASAPI
#define MA_NO_ENCODING
#define MA_NO_DECODING
#define MA_NO_GENERATION
#define MA_NO_RESOURCE_MANAGER
#define MA_NO_NODE_GRAPH
#define MA_NO_ENGINE
#include "miniaudio.h"

#include "vwa_native_output.h"

#define VWA_SEEK_PADDING_BEFORE      (UINT64_C(1) << 0)
#define VWA_SEEK_PADDING_AFTER_STOP  (UINT64_C(1) << 1)
#define VWA_SEEK_PADDING_AFTER_START (UINT64_C(1) << 2)
#define VWA_SEEK_CLOCK_BEFORE        (UINT64_C(1) << 0)
#define VWA_SEEK_CLOCK_AFTER_STOP    (UINT64_C(1) << 1)

#define VWA_DEVICE_ERROR_STOPPED     (-INT64_C(100))
#define VWA_DEVICE_ERROR_INTERRUPTED (-INT64_C(101))
#define VWA_DEVICE_STOPPED_MESSAGE   "WASAPI playback device stopped or was disconnected unexpectedly"

struct VwaOutput
{
    uint64_t backend;
    uint64_t share_mode;
    uint32_t sample_rate;
    uint32_t channels;
    uint32_t capacity_frames;
    uint32_t requested_period_frames;

    float* ring;
    CRITICAL_SECTION control_lock;
    bool lock_initialized;
    atomic_bool closed;

    _Atomic uint64_t read_index;
    _Atomic uint64_t write_index;
    _Atomic uint64_t epoch;
    _Atomic uint64_t accepted_end_frame;
    _Atomic uint64_t media_frame;
    _Atomic uint64_t submitted_frames;
    _Atomic uint64_t callback_frames_last;
    _Atomic uint64_t callback_frames_min;
    _Atomic uint64_t callback_frames_max;
    _Atomic uint64_t callback_count;
    _Atomic uint64_t underrun_frames;
    _Atomic uint64_t xrun_count;
    _Atomic uint64_t pause_frames;
    _Atomic uint64_t stale_epoch_writes;
    _Atomic uint64_t continuity_rejections;
    _Atomic uint64_t partial_writes;
    _Atomic uint64_t seek_count;
    _Atomic uint64_t seek_padding_before;
    _Atomic uint64_t seek_padding_after_stop;
    _Atomic uint64_t seek_padding_after_restart;
    _Atomic uint64_t seek_padding_evidence;
    _Atomic uint64_t ring_owner_violations;
    atomic_uint producer_active;
    atomic_uint consumer_active;
    atomic_uint playing;
    atomic_uint expected_stop;
    atomic_uint notification_started_count;
    atomic_uint notification_stopped_count;
    atomic_uint notification_rerouted_count;
    atomic_uint notification_interruption_count;
    _Atomic int64_t device_error_code;
    _Atomic int64_t last_error_code;

    ma_device device;
    bool device_initialized;
    IAudioClock* p_audio_clock;
    uint64_t actual_device_sample_rate;
    uint64_t actual_device_period_frames;
    uint64_t device_buffer_frames;
    uint64_t default_device_period_100ns;
    uint64_t minimum_device_period_100ns;
    uint64_t stream_latency_100ns;
    uint64_t seek_clock_position_before;
    uint64_t seek_clock_frequency_before;
    uint64_t seek_clock_timestamp_before;
    uint64_t seek_clock_position_after_stop;
    uint64_t seek_clock_frequency_after_stop;
    uint64_t seek_clock_timestamp_after_stop;
    uint64_t seek_clock_evidence;
    uint64_t seek_clock_reset_zero_ok;

    char device_error[256];
    char clock_error[256];
    char last_error[256];
};

static void copy_error(char* destination, size_t capacity, const char* message)
{
    if (destination == NULL || capacity == 0u) {
        return;
    }
    if (message == NULL) {
        message = "";
    }
    (void)snprintf(destination, capacity, "%s", message);
}

static void set_error(char* destination, size_t capacity, const char* message)
{
    copy_error(destination, capacity, message);
}

static void set_handle_last_error(VwaOutput* output, int64_t code, const char* message)
{
    atomic_store_explicit(&output->last_error_code, code, memory_order_release);
    copy_error(output->last_error, sizeof(output->last_error), message);
}

/* Control paths may also set a message. Audio/notification callbacks only set codes. */
static void set_handle_device_error(VwaOutput* output, int64_t code, const char* message)
{
    atomic_store_explicit(&output->device_error_code, code, memory_order_release);
    if (message != NULL && message[0] != '\0') {
        copy_error(output->device_error, sizeof(output->device_error), message);
    }
}

static void set_callback_device_error_code(VwaOutput* output, int64_t code)
{
    atomic_store_explicit(&output->device_error_code, code, memory_order_release);
}

static void add_u64(_Atomic uint64_t* value, uint64_t amount)
{
    (void)atomic_fetch_add_explicit(value, amount, memory_order_relaxed);
}

static void update_callback_period(VwaOutput* output, uint32_t frames)
{
    uint64_t observed;
    uint64_t previous;

    atomic_store_explicit(&output->callback_frames_last, (uint64_t)frames, memory_order_relaxed);
    add_u64(&output->callback_count, UINT64_C(1));

    previous = atomic_load_explicit(&output->callback_frames_min, memory_order_relaxed);
    observed = previous;
    while (observed == 0u || (uint64_t)frames < observed) {
        if (atomic_compare_exchange_weak_explicit(
                &output->callback_frames_min,
                &observed,
                (uint64_t)frames,
                memory_order_relaxed,
                memory_order_relaxed)) {
            break;
        }
    }

    previous = atomic_load_explicit(&output->callback_frames_max, memory_order_relaxed);
    observed = previous;
    while ((uint64_t)frames > observed) {
        if (atomic_compare_exchange_weak_explicit(
                &output->callback_frames_max,
                &observed,
                (uint64_t)frames,
                memory_order_relaxed,
                memory_order_relaxed)) {
            break;
        }
    }
}

static uint64_t queued_frames(const VwaOutput* output)
{
    uint64_t read_index = atomic_load_explicit(&output->read_index, memory_order_acquire);
    uint64_t write_index = atomic_load_explicit(&output->write_index, memory_order_acquire);
    if (write_index < read_index) {
        return 0u;
    }
    return write_index - read_index;
}

static void ring_producer_copy(VwaOutput* output, const float* samples, uint64_t frames)
{
    uint64_t write_index = atomic_load_explicit(&output->write_index, memory_order_relaxed);
    uint64_t remaining = frames;
    const float* source = samples;
    uint64_t channel_count = (uint64_t)output->channels;

    while (remaining > 0u) {
        uint32_t ring_offset = (uint32_t)(write_index % output->capacity_frames);
        uint64_t contiguous = (uint64_t)output->capacity_frames - ring_offset;
        uint64_t chunk = remaining < contiguous ? remaining : contiguous;
        memcpy(
            output->ring + ((size_t)ring_offset * (size_t)channel_count),
            source,
            (size_t)chunk * (size_t)channel_count * sizeof(float));
        write_index += chunk;
        source += chunk * channel_count;
        remaining -= chunk;
    }
    atomic_store_explicit(&output->write_index, write_index, memory_order_release);
}

static uint64_t ring_consumer_copy(VwaOutput* output, float* destination, uint64_t requested_frames)
{
    uint64_t read_index = atomic_load_explicit(&output->read_index, memory_order_relaxed);
    uint64_t write_index = atomic_load_explicit(&output->write_index, memory_order_acquire);
    uint64_t available = write_index >= read_index ? write_index - read_index : 0u;
    uint64_t frames = requested_frames < available ? requested_frames : available;
    uint64_t consumed = 0u;
    uint64_t channel_count = (uint64_t)output->channels;

    while (consumed < frames) {
        uint32_t ring_offset = (uint32_t)(read_index % output->capacity_frames);
        uint64_t contiguous = (uint64_t)output->capacity_frames - ring_offset;
        uint64_t chunk = frames - consumed;
        if (chunk > contiguous) {
            chunk = contiguous;
        }
        memcpy(
            destination + ((size_t)consumed * (size_t)channel_count),
            output->ring + ((size_t)ring_offset * (size_t)channel_count),
            (size_t)chunk * (size_t)channel_count * sizeof(float));
        read_index += chunk;
        consumed += chunk;
    }

    if (frames > 0u) {
        atomic_store_explicit(&output->read_index, read_index, memory_order_release);
        add_u64(&output->media_frame, frames);
    }

    if (frames < requested_frames) {
        uint64_t missing = requested_frames - frames;
        memset(
            destination + ((size_t)frames * (size_t)channel_count),
            0,
            (size_t)missing * (size_t)channel_count * sizeof(float));
        add_u64(&output->underrun_frames, missing);
        add_u64(&output->xrun_count, UINT64_C(1));
    }

    return frames;
}

static uint64_t consume_frames(VwaOutput* output, float* destination, uint32_t frames)
{
    uint64_t channel_count = (uint64_t)output->channels;
    if (frames == 0u) {
        return 0u;
    }

    if (atomic_exchange_explicit(&output->consumer_active, 1u, memory_order_acquire) != 0u) {
        add_u64(&output->ring_owner_violations, UINT64_C(1));
        memset(destination, 0, (size_t)frames * (size_t)channel_count * sizeof(float));
        add_u64(&output->submitted_frames, frames);
        return 0u;
    }

    update_callback_period(output, frames);
    add_u64(&output->submitted_frames, frames);

    if (atomic_load_explicit(&output->playing, memory_order_acquire) == 0u) {
        memset(destination, 0, (size_t)frames * (size_t)channel_count * sizeof(float));
        add_u64(&output->pause_frames, frames);
    } else {
        (void)ring_consumer_copy(output, destination, frames);
    }

    atomic_store_explicit(&output->consumer_active, 0u, memory_order_release);
    return frames;
}

/* Real device callback: no Python, locks, waits, allocation, I/O or device controls. */
static void data_callback(ma_device* device, void* output_buffer, const void* input_buffer, ma_uint32 frame_count)
{
    VwaOutput* output = (VwaOutput*)device->pUserData;
    (void)input_buffer;
    if (output == NULL || output_buffer == NULL) {
        return;
    }
    (void)consume_frames(output, (float*)output_buffer, (uint32_t)frame_count);
}

static void notification_callback(const ma_device_notification* notification)
{
    VwaOutput* output;
    if (notification == NULL || notification->pDevice == NULL) {
        return;
    }
    output = (VwaOutput*)notification->pDevice->pUserData;
    if (output == NULL) {
        return;
    }

    switch (notification->type) {
        case ma_device_notification_type_started:
            atomic_fetch_add_explicit(&output->notification_started_count, 1u, memory_order_relaxed);
            break;
        case ma_device_notification_type_stopped:
            atomic_fetch_add_explicit(&output->notification_stopped_count, 1u, memory_order_relaxed);
            if (atomic_load_explicit(&output->expected_stop, memory_order_acquire) == 0u &&
                !atomic_load_explicit(&output->closed, memory_order_acquire)) {
                set_callback_device_error_code(output, VWA_DEVICE_ERROR_STOPPED);
            }
            break;
        case ma_device_notification_type_rerouted:
            atomic_fetch_add_explicit(&output->notification_rerouted_count, 1u, memory_order_relaxed);
            break;
        case ma_device_notification_type_interruption_began:
        case ma_device_notification_type_interruption_ended:
            atomic_fetch_add_explicit(&output->notification_interruption_count, 1u, memory_order_relaxed);
            if (notification->type == ma_device_notification_type_interruption_began) {
                set_callback_device_error_code(output, VWA_DEVICE_ERROR_INTERRUPTED);
            }
            break;
        case ma_device_notification_type_unlocked:
        default:
            break;
    }
}

/*
 * miniaudio 0.11.26 suppresses the stopped notification when the backend's
 * onDeviceStop() fails, which is the expected path after WASAPI invalidates an
 * endpoint. The worker still leaves the device in stopping/stopped state.
 * Observe that state here on the owner/control thread. This intentionally
 * performs no wait, device control, or string work from the callback.
 */
static bool detect_unexpected_device_stop_locked(VwaOutput* output)
{
    int64_t code;
    ma_device_state state;

    if (output->backend != VWA_OUTPUT_BACKEND_WASAPI || !output->device_initialized) {
        return false;
    }

    code = atomic_load_explicit(&output->device_error_code, memory_order_acquire);
    if (code == 0 &&
        atomic_load_explicit(&output->expected_stop, memory_order_acquire) == 0u &&
        !atomic_load_explicit(&output->closed, memory_order_acquire)) {
        state = ma_device_get_state(&output->device);
        if (state == ma_device_state_stopping || state == ma_device_state_stopped) {
            set_handle_device_error(output, VWA_DEVICE_ERROR_STOPPED, VWA_DEVICE_STOPPED_MESSAGE);
            code = VWA_DEVICE_ERROR_STOPPED;
        }
    }

    if (code != 0) {
        atomic_store_explicit(&output->playing, 0u, memory_order_release);
        return true;
    }
    return false;
}

static void format_hresult(char* destination, size_t capacity, const char* operation, HRESULT hr)
{
    if (destination == NULL || capacity == 0u) {
        return;
    }
    (void)snprintf(destination, capacity, "%s failed: HRESULT 0x%08lX", operation, (unsigned long)hr);
}

static BOOL query_padding_locked(VwaOutput* output, uint64_t* padding)
{
    ma_IAudioClient* client;
    ma_uint32 current_padding = 0u;
    HRESULT hr;

    if (padding != NULL) {
        *padding = 0u;
    }
    if (output->backend != VWA_OUTPUT_BACKEND_WASAPI || !output->device_initialized) {
        return FALSE;
    }
    client = (ma_IAudioClient*)output->device.wasapi.pAudioClientPlayback;
    if (client == NULL) {
        return FALSE;
    }
    hr = ma_IAudioClient_GetCurrentPadding(client, &current_padding);
    if (FAILED(hr)) {
        format_hresult(output->clock_error, sizeof(output->clock_error), "IAudioClient::GetCurrentPadding", hr);
        return FALSE;
    }
    if (padding != NULL) {
        *padding = (uint64_t)current_padding;
    }
    return TRUE;
}

static BOOL query_clock_raw_locked(
    VwaOutput* output,
    uint64_t* position,
    uint64_t* frequency,
    uint64_t* timestamp)
{
    UINT64 raw_position = 0u;
    UINT64 raw_frequency = 0u;
    UINT64 raw_timestamp = 0u;
    HRESULT hr;

    if (position != NULL) {
        *position = 0u;
    }
    if (frequency != NULL) {
        *frequency = 0u;
    }
    if (timestamp != NULL) {
        *timestamp = 0u;
    }
    if (output->p_audio_clock == NULL) {
        return FALSE;
    }

    hr = output->p_audio_clock->lpVtbl->GetFrequency(output->p_audio_clock, &raw_frequency);
    if (FAILED(hr)) {
        format_hresult(output->clock_error, sizeof(output->clock_error), "IAudioClock::GetFrequency", hr);
        return FALSE;
    }
    hr = output->p_audio_clock->lpVtbl->GetPosition(output->p_audio_clock, &raw_position, &raw_timestamp);
    if (FAILED(hr)) {
        format_hresult(output->clock_error, sizeof(output->clock_error), "IAudioClock::GetPosition", hr);
        return FALSE;
    }

    if (position != NULL) {
        *position = (uint64_t)raw_position;
    }
    if (frequency != NULL) {
        *frequency = (uint64_t)raw_frequency;
    }
    if (timestamp != NULL) {
        *timestamp = (uint64_t)raw_timestamp;
    }
    return TRUE;
}

static void query_clock_locked(VwaOutput* output, VwaOutputSnapshot* snapshot)
{
    UINT64 position = 0u;
    UINT64 frequency = 0u;
    UINT64 timestamp = 0u;

    if (output->p_audio_clock == NULL) {
        snapshot->wasapi_clock_query_ok = 0u;
        snapshot->reserved_clock_characteristics_query_ok = 0u;
        if (output->clock_error[0] == '\0') {
            copy_error(output->clock_error, sizeof(output->clock_error),
                       "IAudioClock service unavailable in the pinned miniaudio device stream");
        }
        return;
    }

    if (!query_clock_raw_locked(output, &position, &frequency, &timestamp)) {
        return;
    }

    snapshot->wasapi_clock_frequency = (uint64_t)frequency;
    snapshot->wasapi_clock_position = (uint64_t)position;
    snapshot->wasapi_clock_timestamp = (uint64_t)timestamp;
    snapshot->wasapi_clock_query_ok = 1u;


}

static void query_static_wasapi_metadata_locked(VwaOutput* output)
{
    ma_IAudioClient* client;
    MA_REFERENCE_TIME default_period = 0;
    MA_REFERENCE_TIME minimum_period = 0;
    MA_REFERENCE_TIME latency = 0;
    HRESULT hr;

    if (output->backend != VWA_OUTPUT_BACKEND_WASAPI || !output->device_initialized) {
        return;
    }

    output->actual_device_sample_rate = (uint64_t)output->device.playback.internalSampleRate;
    output->actual_device_period_frames = (uint64_t)output->device.playback.internalPeriodSizeInFrames;
    output->device_buffer_frames = (uint64_t)output->device.wasapi.actualBufferSizeInFramesPlayback;

    client = (ma_IAudioClient*)output->device.wasapi.pAudioClientPlayback;
    if (client == NULL) {
        return;
    }

    hr = ma_IAudioClient_GetDevicePeriod(client, &default_period, &minimum_period);
    if (SUCCEEDED(hr)) {
        output->default_device_period_100ns = (uint64_t)default_period;
        output->minimum_device_period_100ns = (uint64_t)minimum_period;
    } else {
        format_hresult(output->clock_error, sizeof(output->clock_error), "IAudioClient::GetDevicePeriod", hr);
    }

    hr = ma_IAudioClient_GetStreamLatency(client, &latency);
    if (SUCCEEDED(hr)) {
        output->stream_latency_100ns = (uint64_t)latency;
    } else {
        format_hresult(output->clock_error, sizeof(output->clock_error), "IAudioClient::GetStreamLatency", hr);
    }
}

static BOOL acquire_audio_clock_locked(VwaOutput* output)
{
    ma_IAudioClient* client;
    void* service = NULL;
    HRESULT hr;

    if (output->backend != VWA_OUTPUT_BACKEND_WASAPI || !output->device_initialized) {
        return FALSE;
    }
    client = (ma_IAudioClient*)output->device.wasapi.pAudioClientPlayback;
    if (client == NULL) {
        return FALSE;
    }

    hr = ma_IAudioClient_GetService(client, &IID_IAudioClock, &service);
    if (FAILED(hr) || service == NULL) {
        format_hresult(output->clock_error, sizeof(output->clock_error),
                       "IAudioClient::GetService(IAudioClock)", hr);
        return FALSE;
    }
    output->p_audio_clock = (IAudioClock*)service;
    return TRUE;
}

static void release_audio_clock_locked(VwaOutput* output)
{
    if (output->p_audio_clock != NULL) {
        (void)output->p_audio_clock->lpVtbl->Release(output->p_audio_clock);
        output->p_audio_clock = NULL;
    }
}

static void reset_ring_locked(VwaOutput* output)
{
    uint64_t write_index = atomic_load_explicit(&output->write_index, memory_order_acquire);
    atomic_store_explicit(&output->read_index, write_index, memory_order_release);
}

static int initialize_wasapi(VwaOutput* output, char* error_text, size_t error_text_capacity)
{
    ma_device_config config;
    ma_result result;

    config = ma_device_config_init(ma_device_type_playback);
    config.playback.format = ma_format_f32;
    config.playback.channels = output->channels;
    config.playback.pDeviceID = NULL;
    config.playback.shareMode = ma_share_mode_shared;
    config.sampleRate = output->sample_rate;
    config.periodSizeInFrames = output->requested_period_frames;
    config.periods = 2u;
    config.performanceProfile = ma_performance_profile_low_latency;
    config.noPreSilencedOutputBuffer = MA_FALSE;
    config.noFixedSizedCallback = MA_FALSE;
    config.wasapi.noAutoStreamRouting = MA_TRUE;
    config.dataCallback = data_callback;
    config.notificationCallback = notification_callback;
    config.pUserData = output;

    result = ma_device_init(NULL, &config, &output->device);
    if (result != MA_SUCCESS) {
        char message[256];
        (void)snprintf(
            message,
            sizeof(message),
            "ma_device_init(shared WASAPI) failed: %s (%d)",
            ma_result_description(result),
            (int)result);
        set_handle_device_error(output, -200 + (int64_t)result, message);
        set_error(error_text, error_text_capacity, message);
        return VWA_OUTPUT_ERR_DEVICE_INIT;
    }
    output->device_initialized = true;
    output->share_mode = VWA_OUTPUT_SHARE_SHARED;

    query_static_wasapi_metadata_locked(output);
    (void)acquire_audio_clock_locked(output);

    atomic_store_explicit(&output->expected_stop, 1u, memory_order_release);
    result = ma_device_start(&output->device);
    atomic_store_explicit(&output->expected_stop, 0u, memory_order_release);
    if (result != MA_SUCCESS) {
        char message[256];
        (void)snprintf(
            message,
            sizeof(message),
            "ma_device_start(shared WASAPI) failed: %s (%d)",
            ma_result_description(result),
            (int)result);
        set_handle_device_error(output, -300 + (int64_t)result, message);
        set_error(error_text, error_text_capacity, message);
        return VWA_OUTPUT_ERR_DEVICE_START;
    }
    if (detect_unexpected_device_stop_locked(output)) {
        set_error(error_text, error_text_capacity, output->device_error);
        return VWA_OUTPUT_ERR_DEVICE_STOP;
    }

    return VWA_OUTPUT_OK;
}

const char* vwa_output_abi_string(void)
{
    return "vwa-native-output-abi-1/miniaudio-0.11.26";
}

uint64_t vwa_output_snapshot_size(void)
{
    return (uint64_t)sizeof(VwaOutputSnapshot);
}

int vwa_output_create(
    uint32_t mode,
    uint32_t sample_rate,
    uint32_t channels,
    uint32_t capacity_frames,
    uint32_t period_frames,
    VwaOutput** out_output,
    char* error_text,
    size_t error_text_capacity)
{
    VwaOutput* output;
    size_t sample_count;
    int result;

    if (error_text != NULL && error_text_capacity > 0u) {
        error_text[0] = '\0';
    }
    if (out_output == NULL) {
        set_error(error_text, error_text_capacity, "out_output is null");
        return VWA_OUTPUT_ERR_INVALID_ARGUMENT;
    }
    *out_output = NULL;
    if (mode > VWA_OUTPUT_BACKEND_WASAPI ||
        sample_rate < 1000u || sample_rate > 384000u ||
        channels == 0u || channels > 8u ||
        capacity_frames == 0u || capacity_frames > 4194304u ||
        period_frames == 0u || period_frames > capacity_frames) {
        set_error(error_text, error_text_capacity, "invalid native output configuration");
        return VWA_OUTPUT_ERR_INVALID_ARGUMENT;
    }

    output = (VwaOutput*)calloc(1u, sizeof(*output));
    if (output == NULL) {
        set_error(error_text, error_text_capacity, "allocation failed for native output");
        return VWA_OUTPUT_ERR_ALLOCATION;
    }
    output->backend = mode;
    output->share_mode = mode == VWA_OUTPUT_BACKEND_WASAPI
        ? VWA_OUTPUT_SHARE_SHARED
        : VWA_OUTPUT_SHARE_NONE;
    output->sample_rate = sample_rate;
    output->channels = channels;
    output->capacity_frames = capacity_frames;
    output->requested_period_frames = period_frames;

    sample_count = (size_t)capacity_frames * (size_t)channels;
    if (sample_count > SIZE_MAX / sizeof(float)) {
        set_error(error_text, error_text_capacity, "ring allocation size overflow");
        free(output);
        return VWA_OUTPUT_ERR_ALLOCATION;
    }
    output->ring = (float*)malloc(sample_count * sizeof(float));
    if (output->ring == NULL) {
        set_error(error_text, error_text_capacity, "allocation failed for native PCM ring");
        free(output);
        return VWA_OUTPUT_ERR_ALLOCATION;
    }

    InitializeCriticalSection(&output->control_lock);
    output->lock_initialized = true;
    atomic_init(&output->closed, false);
    atomic_init(&output->read_index, 0u);
    atomic_init(&output->write_index, 0u);
    atomic_init(&output->epoch, 0u);
    atomic_init(&output->accepted_end_frame, 0u);
    atomic_init(&output->media_frame, 0u);
    atomic_init(&output->submitted_frames, 0u);
    atomic_init(&output->callback_frames_last, 0u);
    atomic_init(&output->callback_frames_min, 0u);
    atomic_init(&output->callback_frames_max, 0u);
    atomic_init(&output->callback_count, 0u);
    atomic_init(&output->underrun_frames, 0u);
    atomic_init(&output->xrun_count, 0u);
    atomic_init(&output->pause_frames, 0u);
    atomic_init(&output->stale_epoch_writes, 0u);
    atomic_init(&output->continuity_rejections, 0u);
    atomic_init(&output->partial_writes, 0u);
    atomic_init(&output->seek_count, 0u);
    atomic_init(&output->seek_padding_before, 0u);
    atomic_init(&output->seek_padding_after_stop, 0u);
    atomic_init(&output->seek_padding_after_restart, 0u);
    atomic_init(&output->seek_padding_evidence, 0u);
    atomic_init(&output->ring_owner_violations, 0u);
    atomic_init(&output->producer_active, 0u);
    atomic_init(&output->consumer_active, 0u);
    atomic_init(&output->playing, 0u);
    atomic_init(&output->expected_stop, 0u);
    atomic_init(&output->notification_started_count, 0u);
    atomic_init(&output->notification_stopped_count, 0u);
    atomic_init(&output->notification_rerouted_count, 0u);
    atomic_init(&output->notification_interruption_count, 0u);
    atomic_init(&output->device_error_code, 0);
    atomic_init(&output->last_error_code, 0);

    if (mode == VWA_OUTPUT_BACKEND_WASAPI) {
        result = initialize_wasapi(output, error_text, error_text_capacity);
        if (result != VWA_OUTPUT_OK) {
            atomic_store_explicit(&output->closed, true, memory_order_release);
            if (output->device_initialized) {
                atomic_store_explicit(&output->expected_stop, 1u, memory_order_release);
                if (ma_device_get_state(&output->device) == ma_device_state_started) {
                    (void)ma_device_stop(&output->device);
                }
                release_audio_clock_locked(output);
                ma_device_uninit(&output->device);
                output->device_initialized = false;
            }
            DeleteCriticalSection(&output->control_lock);
            free(output->ring);
            free(output);
            return result;
        }
    }

    *out_output = output;
    return VWA_OUTPUT_OK;
}

void vwa_output_destroy(VwaOutput* output)
{
    if (output == NULL) {
        return;
    }
    EnterCriticalSection(&output->control_lock);
    if (atomic_exchange_explicit(&output->closed, true, memory_order_acq_rel)) {
        LeaveCriticalSection(&output->control_lock);
        return;
    }
    atomic_store_explicit(&output->expected_stop, 1u, memory_order_release);
    if (output->device_initialized) {
        if (ma_device_get_state(&output->device) == ma_device_state_started) {
            (void)ma_device_stop(&output->device);
        }
        release_audio_clock_locked(output);
        ma_device_uninit(&output->device);
        output->device_initialized = false;
    }
    LeaveCriticalSection(&output->control_lock);
    DeleteCriticalSection(&output->control_lock);
    free(output->ring);
    free(output);
}

uint64_t vwa_output_write(
    VwaOutput* output,
    const float* samples,
    uint64_t frames,
    uint64_t epoch,
    uint64_t start_frame)
{
    uint64_t queued;
    uint64_t available;
    uint64_t accepted;
    uint64_t current_epoch;
    uint64_t expected_start;

    if (output == NULL || samples == NULL || frames == 0u) {
        return 0u;
    }
    EnterCriticalSection(&output->control_lock);
    if (atomic_load_explicit(&output->closed, memory_order_acquire)) {
        set_handle_last_error(output, VWA_OUTPUT_ERR_CLOSED, "write on closed native output");
        LeaveCriticalSection(&output->control_lock);
        return 0u;
    }
    if (detect_unexpected_device_stop_locked(output)) {
        set_handle_last_error(output, VWA_OUTPUT_ERR_DEVICE_STOP,
                              "write rejected: WASAPI device is stopped or disconnected");
        LeaveCriticalSection(&output->control_lock);
        return 0u;
    }
    if (atomic_exchange_explicit(&output->producer_active, 1u, memory_order_acquire) != 0u) {
        add_u64(&output->ring_owner_violations, UINT64_C(1));
        set_handle_last_error(output, VWA_OUTPUT_ERR_INVALID_ARGUMENT, "concurrent producer ring access");
        LeaveCriticalSection(&output->control_lock);
        return 0u;
    }

    current_epoch = atomic_load_explicit(&output->epoch, memory_order_acquire);
    expected_start = atomic_load_explicit(&output->accepted_end_frame, memory_order_acquire);
    if (epoch != current_epoch) {
        add_u64(&output->stale_epoch_writes, UINT64_C(1));
        set_handle_last_error(output, VWA_OUTPUT_ERR_STALE_EPOCH, "write rejected: epoch is not current");
        atomic_store_explicit(&output->producer_active, 0u, memory_order_release);
        LeaveCriticalSection(&output->control_lock);
        return 0u;
    }
    if (start_frame != expected_start) {
        add_u64(&output->continuity_rejections, UINT64_C(1));
        set_handle_last_error(output, VWA_OUTPUT_ERR_INVALID_ARGUMENT,
                              "write rejected: start_frame is not contiguous with accepted tail");
        atomic_store_explicit(&output->producer_active, 0u, memory_order_release);
        LeaveCriticalSection(&output->control_lock);
        return 0u;
    }

    queued = queued_frames(output);
    available = queued <= (uint64_t)output->capacity_frames
        ? (uint64_t)output->capacity_frames - queued
        : 0u;
    accepted = frames < available ? frames : available;
    if (accepted > 0u) {
        ring_producer_copy(output, samples, accepted);
        atomic_store_explicit(&output->accepted_end_frame, expected_start + accepted, memory_order_release);
    }
    if (accepted < frames) {
        add_u64(&output->partial_writes, UINT64_C(1));
        set_handle_last_error(
            output,
            VWA_OUTPUT_OK,
            accepted == 0u ? "PCM ring is full" : "PCM ring accepted a partial write");
    } else {
        set_handle_last_error(output, VWA_OUTPUT_OK, "");
    }

    atomic_store_explicit(&output->producer_active, 0u, memory_order_release);
    LeaveCriticalSection(&output->control_lock);
    return accepted;
}

int vwa_output_seek(
    VwaOutput* output,
    uint64_t frame,
    uint64_t epoch,
    char* error_text,
    size_t error_text_capacity)
{
    uint64_t current_epoch;
    uint64_t padding = 0u;
    uint64_t evidence = 0u;
    uint64_t clock_evidence = 0u;
    uint64_t clock_position = 0u;
    uint64_t clock_frequency = 0u;
    uint64_t clock_timestamp = 0u;
    int return_code = VWA_OUTPUT_OK;

    if (error_text != NULL && error_text_capacity > 0u) {
        error_text[0] = '\0';
    }
    if (output == NULL) {
        set_error(error_text, error_text_capacity, "native output is null");
        return VWA_OUTPUT_ERR_INVALID_ARGUMENT;
    }
    EnterCriticalSection(&output->control_lock);
    if (atomic_load_explicit(&output->closed, memory_order_acquire)) {
        set_error(error_text, error_text_capacity, "seek on closed native output");
        return_code = VWA_OUTPUT_ERR_CLOSED;
        goto done;
    }
    if (detect_unexpected_device_stop_locked(output)) {
        set_error(error_text, error_text_capacity, output->device_error);
        set_handle_last_error(output, VWA_OUTPUT_ERR_DEVICE_STOP,
                              "seek rejected: WASAPI device is stopped or disconnected");
        return_code = VWA_OUTPUT_ERR_DEVICE_STOP;
        goto done;
    }
    current_epoch = atomic_load_explicit(&output->epoch, memory_order_acquire);
    if (epoch <= current_epoch) {
        add_u64(&output->stale_epoch_writes, UINT64_C(1));
        set_error(error_text, error_text_capacity, "seek rejected: epoch must be strictly newer");
        set_handle_last_error(output, VWA_OUTPUT_ERR_STALE_EPOCH,
                              "seek rejected: epoch must be strictly newer");
        return_code = VWA_OUTPUT_ERR_STALE_EPOCH;
        goto done;
    }

    output->seek_clock_position_before = 0u;
    output->seek_clock_frequency_before = 0u;
    output->seek_clock_timestamp_before = 0u;
    output->seek_clock_position_after_stop = 0u;
    output->seek_clock_frequency_after_stop = 0u;
    output->seek_clock_timestamp_after_stop = 0u;
    output->seek_clock_evidence = 0u;
    output->seek_clock_reset_zero_ok = 0u;

    if (output->backend == VWA_OUTPUT_BACKEND_WASAPI && output->device_initialized) {
        if (query_padding_locked(output, &padding)) {
            evidence |= VWA_SEEK_PADDING_BEFORE;
            atomic_store_explicit(&output->seek_padding_before, padding, memory_order_release);
        } else {
            atomic_store_explicit(&output->seek_padding_before, 0u, memory_order_release);
        }
        if (query_clock_raw_locked(output, &clock_position, &clock_frequency, &clock_timestamp)) {
            clock_evidence |= VWA_SEEK_CLOCK_BEFORE;
            output->seek_clock_position_before = clock_position;
            output->seek_clock_frequency_before = clock_frequency;
            output->seek_clock_timestamp_before = clock_timestamp;
        }

        atomic_store_explicit(&output->expected_stop, 1u, memory_order_release);
        if (ma_device_stop(&output->device) != MA_SUCCESS) {
            atomic_store_explicit(&output->expected_stop, 0u, memory_order_release);
            set_handle_device_error(output, -400, "ma_device_stop failed during seek quiesce");
            set_error(error_text, error_text_capacity, "ma_device_stop failed during seek quiesce");
            set_handle_last_error(output, VWA_OUTPUT_ERR_DEVICE_STOP,
                                  "ma_device_stop failed during seek quiesce");
            return_code = VWA_OUTPUT_ERR_DEVICE_STOP;
            goto done;
        }
        if (query_padding_locked(output, &padding)) {
            evidence |= VWA_SEEK_PADDING_AFTER_STOP;
            atomic_store_explicit(&output->seek_padding_after_stop, padding, memory_order_release);
        } else {
            atomic_store_explicit(&output->seek_padding_after_stop, 0u, memory_order_release);
        }
        /*
         * miniaudio's WASAPI stop path drains while started, calls Stop(), then
         * Reset() after the device thread is quiescent. Only query clock after
         * that complete operation; no buffer operation remains concurrent.
         */
        if (query_clock_raw_locked(output, &clock_position, &clock_frequency, &clock_timestamp)) {
            clock_evidence |= VWA_SEEK_CLOCK_AFTER_STOP;
            output->seek_clock_position_after_stop = clock_position;
            output->seek_clock_frequency_after_stop = clock_frequency;
            output->seek_clock_timestamp_after_stop = clock_timestamp;
            output->seek_clock_reset_zero_ok = clock_position == 0u ? 1u : 0u;
        }
    }

    reset_ring_locked(output);
    atomic_store_explicit(&output->accepted_end_frame, frame, memory_order_release);
    atomic_store_explicit(&output->media_frame, frame, memory_order_release);
    atomic_store_explicit(&output->epoch, epoch, memory_order_release);
    add_u64(&output->seek_count, UINT64_C(1));

    if (output->backend == VWA_OUTPUT_BACKEND_WASAPI && output->device_initialized) {
        ma_result start_result;
        atomic_store_explicit(&output->expected_stop, 1u, memory_order_release);
        start_result = ma_device_start(&output->device);
        atomic_store_explicit(&output->expected_stop, 0u, memory_order_release);
        if (start_result != MA_SUCCESS) {
            set_handle_device_error(output, -500 + (int64_t)start_result,
                                    "ma_device_start failed after seek quiesce");
            set_error(error_text, error_text_capacity, "ma_device_start failed after seek quiesce");
            set_handle_last_error(output, VWA_OUTPUT_ERR_DEVICE_START,
                                  "ma_device_start failed after seek quiesce");
            return_code = VWA_OUTPUT_ERR_DEVICE_START;
            goto done;
        }
        if (detect_unexpected_device_stop_locked(output)) {
            set_error(error_text, error_text_capacity, output->device_error);
            set_handle_last_error(output, VWA_OUTPUT_ERR_DEVICE_STOP, output->device_error);
            return_code = VWA_OUTPUT_ERR_DEVICE_STOP;
            goto done;
        }
        if (query_padding_locked(output, &padding)) {
            evidence |= VWA_SEEK_PADDING_AFTER_START;
            atomic_store_explicit(&output->seek_padding_after_restart, padding, memory_order_release);
        } else {
            atomic_store_explicit(&output->seek_padding_after_restart, 0u, memory_order_release);
        }
    }

    atomic_store_explicit(&output->seek_padding_evidence, evidence, memory_order_release);
    output->seek_clock_evidence = clock_evidence;
    set_handle_last_error(output, VWA_OUTPUT_OK, "");

done:
    LeaveCriticalSection(&output->control_lock);
    return return_code;
}

int vwa_output_set_playing(
    VwaOutput* output,
    uint32_t playing,
    char* error_text,
    size_t error_text_capacity)
{
    if (error_text != NULL && error_text_capacity > 0u) {
        error_text[0] = '\0';
    }
    if (output == NULL) {
        set_error(error_text, error_text_capacity, "native output is null");
        return VWA_OUTPUT_ERR_INVALID_ARGUMENT;
    }
    EnterCriticalSection(&output->control_lock);
    if (atomic_load_explicit(&output->closed, memory_order_acquire)) {
        set_error(error_text, error_text_capacity, "set_playing on closed native output");
        LeaveCriticalSection(&output->control_lock);
        return VWA_OUTPUT_ERR_CLOSED;
    }
    if (detect_unexpected_device_stop_locked(output)) {
        set_error(error_text, error_text_capacity, output->device_error);
        set_handle_last_error(output, VWA_OUTPUT_ERR_DEVICE_STOP,
                              "set_playing rejected: WASAPI device is stopped or disconnected");
        LeaveCriticalSection(&output->control_lock);
        return VWA_OUTPUT_ERR_DEVICE_STOP;
    }
    atomic_store_explicit(&output->playing, playing != 0u ? 1u : 0u, memory_order_release);
    LeaveCriticalSection(&output->control_lock);
    return VWA_OUTPUT_OK;
}

int vwa_output_snapshot(
    VwaOutput* output,
    VwaOutputSnapshot* snapshot,
    char* error_text,
    size_t error_text_capacity)
{
    uint64_t padding = 0u;
    int64_t device_error_code;

    if (error_text != NULL && error_text_capacity > 0u) {
        error_text[0] = '\0';
    }
    if (snapshot == NULL || output == NULL) {
        set_error(error_text, error_text_capacity, "snapshot or native output is null");
        return VWA_OUTPUT_ERR_INVALID_ARGUMENT;
    }

    memset(snapshot, 0, sizeof(*snapshot));
    EnterCriticalSection(&output->control_lock);
    (void)detect_unexpected_device_stop_locked(output);

    snapshot->abi_version = VWA_OUTPUT_ABI_VERSION;
    snapshot->backend = output->backend;
    snapshot->share_mode = output->share_mode;
    snapshot->epoch = atomic_load_explicit(&output->epoch, memory_order_acquire);
    snapshot->capacity_frames = output->capacity_frames;
    snapshot->queued_frames = queued_frames(output);
    snapshot->media_frame = atomic_load_explicit(&output->media_frame, memory_order_acquire);
    snapshot->accepted_end_frame = atomic_load_explicit(&output->accepted_end_frame, memory_order_acquire);
    snapshot->submitted_frames = atomic_load_explicit(&output->submitted_frames, memory_order_acquire);
    snapshot->sample_rate = output->sample_rate;
    snapshot->channels = output->channels;
    snapshot->period_frames = output->requested_period_frames;
    snapshot->requested_period_frames = output->requested_period_frames;
    snapshot->actual_device_sample_rate = output->actual_device_sample_rate;
    snapshot->actual_device_period_frames = output->actual_device_period_frames;
    snapshot->device_buffer_frames = output->device_buffer_frames;
    snapshot->callback_frames_last = atomic_load_explicit(&output->callback_frames_last, memory_order_acquire);
    snapshot->callback_frames_min = atomic_load_explicit(&output->callback_frames_min, memory_order_acquire);
    snapshot->callback_frames_max = atomic_load_explicit(&output->callback_frames_max, memory_order_acquire);
    snapshot->callback_count = atomic_load_explicit(&output->callback_count, memory_order_acquire);
    snapshot->underrun_frames = atomic_load_explicit(&output->underrun_frames, memory_order_acquire);
    snapshot->xrun_count = atomic_load_explicit(&output->xrun_count, memory_order_acquire);
    snapshot->pause_frames = atomic_load_explicit(&output->pause_frames, memory_order_acquire);
    snapshot->stale_epoch_writes = atomic_load_explicit(&output->stale_epoch_writes, memory_order_acquire);
    snapshot->continuity_rejections = atomic_load_explicit(&output->continuity_rejections, memory_order_acquire);
    snapshot->partial_writes = atomic_load_explicit(&output->partial_writes, memory_order_acquire);
    snapshot->seek_count = atomic_load_explicit(&output->seek_count, memory_order_acquire);
    snapshot->seek_padding_before = atomic_load_explicit(&output->seek_padding_before, memory_order_acquire);
    snapshot->seek_padding_after_stop = atomic_load_explicit(&output->seek_padding_after_stop, memory_order_acquire);
    snapshot->seek_padding_after_restart = atomic_load_explicit(&output->seek_padding_after_restart, memory_order_acquire);
    snapshot->seek_padding_evidence = atomic_load_explicit(&output->seek_padding_evidence, memory_order_acquire);
    snapshot->seek_clock_position_before = output->seek_clock_position_before;
    snapshot->seek_clock_frequency_before = output->seek_clock_frequency_before;
    snapshot->seek_clock_timestamp_before = output->seek_clock_timestamp_before;
    snapshot->seek_clock_position_after_stop = output->seek_clock_position_after_stop;
    snapshot->seek_clock_frequency_after_stop = output->seek_clock_frequency_after_stop;
    snapshot->seek_clock_timestamp_after_stop = output->seek_clock_timestamp_after_stop;
    snapshot->seek_clock_evidence = output->seek_clock_evidence;
    snapshot->seek_clock_reset_zero_ok = output->seek_clock_reset_zero_ok;
    snapshot->wasapi_default_device_period_100ns = output->default_device_period_100ns;
    snapshot->wasapi_minimum_device_period_100ns = output->minimum_device_period_100ns;
    snapshot->wasapi_stream_latency_100ns = output->stream_latency_100ns;
    snapshot->playing = atomic_load_explicit(&output->playing, memory_order_acquire);
    snapshot->closing = atomic_load_explicit(&output->closed, memory_order_acquire) ? 1u : 0u;
    snapshot->ring_owner_violations = atomic_load_explicit(&output->ring_owner_violations, memory_order_acquire);
    snapshot->device_error_code = atomic_load_explicit(&output->device_error_code, memory_order_acquire);
    snapshot->error_code = atomic_load_explicit(&output->last_error_code, memory_order_acquire);

    if (output->backend == VWA_OUTPUT_BACKEND_WASAPI && output->device_initialized) {
        if (query_padding_locked(output, &padding)) {
            snapshot->wasapi_padding_frames = padding;
            snapshot->wasapi_padding_query_ok = 1u;
        }
        query_clock_locked(output, snapshot);
    }

    device_error_code = snapshot->device_error_code;
    copy_error(snapshot->device_error, sizeof(snapshot->device_error), output->device_error);
    if (snapshot->device_error[0] == '\0' && device_error_code != 0) {
        if (device_error_code == VWA_DEVICE_ERROR_STOPPED) {
            copy_error(snapshot->device_error, sizeof(snapshot->device_error),
                       VWA_DEVICE_STOPPED_MESSAGE);
        } else if (device_error_code == VWA_DEVICE_ERROR_INTERRUPTED) {
            copy_error(snapshot->device_error, sizeof(snapshot->device_error),
                       "WASAPI device interruption began");
        } else {
            (void)snprintf(snapshot->device_error, sizeof(snapshot->device_error),
                           "native device error code %" PRId64, device_error_code);
        }
    }
    copy_error(snapshot->clock_error, sizeof(snapshot->clock_error), output->clock_error);
    copy_error(snapshot->last_error, sizeof(snapshot->last_error), output->last_error);
    LeaveCriticalSection(&output->control_lock);
    return VWA_OUTPUT_OK;
}

uint64_t vwa_output_pull(
    VwaOutput* output,
    float* samples,
    uint64_t frames)
{
    uint64_t pulled;
    if (output == NULL || samples == NULL || frames == 0u || frames > UINT32_MAX) {
        return 0u;
    }
    EnterCriticalSection(&output->control_lock);
    if (atomic_load_explicit(&output->closed, memory_order_acquire) ||
        output->backend != VWA_OUTPUT_BACKEND_NULL) {
        LeaveCriticalSection(&output->control_lock);
        return 0u;
    }
    pulled = consume_frames(output, samples, (uint32_t)frames);
    LeaveCriticalSection(&output->control_lock);
    return pulled;
}
