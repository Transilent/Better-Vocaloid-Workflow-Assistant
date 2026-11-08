#ifndef VWA_LIMITER_H
#define VWA_LIMITER_H

#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32) && (defined(VWA_LIMITER_BUILD_DLL) || defined(VWA_OUTPUT_BUILD_DLL))
#define VWA_LIMITER_API __declspec(dllexport)
#elif defined(_WIN32) && defined(VWA_LIMITER_USE_DLL)
#define VWA_LIMITER_API __declspec(dllimport)
#else
#define VWA_LIMITER_API
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define VWA_LIMITER_ABI_VERSION 1u
#define VWA_LIMITER_ATTACK_MS 5.0
#define VWA_LIMITER_RELEASE_MS 100.0
#define VWA_LIMITER_LIMIT_DBFS (-1.0)
#define VWA_LIMITER_LIMIT 0.891250938

enum {
    VWA_LIMITER_OK = 0,
    VWA_LIMITER_ERR_INVALID_ARGUMENT = -1,
    VWA_LIMITER_ERR_ALLOCATION = -2,
    VWA_LIMITER_ERR_BUFFER_TOO_SMALL = -3,
    VWA_LIMITER_ERR_NONFINITE_INPUT = -4,
    VWA_LIMITER_ERR_FINISHED = -5
};

typedef struct VwaLimiter VwaLimiter;

VWA_LIMITER_API const char* vwa_limiter_abi_string(void);

/* Returns floor(sample_rate * 5ms), matching FFmpeg alimiter's attack buffer. */
VWA_LIMITER_API uint32_t vwa_limiter_attack_frames(uint32_t sample_rate);

VWA_LIMITER_API int vwa_limiter_create(
    uint32_t sample_rate,
    uint32_t channels,
    VwaLimiter** out_limiter,
    char* error_text,
    size_t error_text_capacity);

VWA_LIMITER_API void vwa_limiter_destroy(VwaLimiter* limiter);

/*
 * Consumes all input frames and emits committed frames. The first
 * attack_frames - 1 frames are withheld like FFmpeg alimiter latency=true.
 * out_capacity is expressed in frames and must be at least input_frames.
 * Interleaved float32 samples are used. input_samples and output_samples may
 * alias. On error the limiter state and out_frames are unchanged.
 */
VWA_LIMITER_API int vwa_limiter_process(
    VwaLimiter* limiter,
    const float* input_samples,
    uint64_t input_frames,
    float* output_samples,
    uint64_t output_capacity_frames,
    uint64_t* out_frames,
    char* error_text,
    size_t error_text_capacity);

/*
 * Sends the same attack_frames - 1 zero-input frames as FFmpeg latency=true
 * EOF padding, then marks the stream finished. output_capacity_frames must fit
 * all frames still owed by this stream. A second call returns zero frames.
 */
VWA_LIMITER_API int vwa_limiter_drain(
    VwaLimiter* limiter,
    float* output_samples,
    uint64_t output_capacity_frames,
    uint64_t* out_frames,
    char* error_text,
    size_t error_text_capacity);

/* Clears all delay, gain and stream-finished state. */
VWA_LIMITER_API int vwa_limiter_reset(
    VwaLimiter* limiter,
    char* error_text,
    size_t error_text_capacity);

#ifdef __cplusplus
}
#endif

#endif