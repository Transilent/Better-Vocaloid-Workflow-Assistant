cmake_minimum_required(VERSION 3.20)
project(vwa_native VERSION 2.1.0 LANGUAGES C CXX)

include(FetchContent)
include(CTest)

# Pinned third-party header dependencies (single source of truth).
set(VWA_MINIAUDIO_VERSION "0.11.26")
set(VWA_MINIAUDIO_COMMIT "877ea4a7c2601c7b0f2a44e83dfac42e6470fea6")
set(VWA_MINIAUDIO_HEADER_SHA256 "6B376C7FBB1352BA1E0E432AB66069B335EA9405E45CB6C30B97EF0F3C9C3E0E")

set(VWA_SIGNALSMITH_STRETCH_COMMIT "a670068d9aeb64913331d5cc29337b19a457a7df")
set(VWA_SIGNALSMITH_LINEAR_COMMIT "de55e6a50ffcf6f8f43f649692d94691c7025151")

# Optional offline / pre-fetched local directories (avoiding hardcoded machine paths).
set(VWA_MINIAUDIO_DIR "" CACHE PATH "Directory containing pinned miniaudio.h (0.11.26)")
set(VWA_SIGNALSMITH_STRETCH_DIR "" CACHE PATH "Directory containing pinned signalsmith-stretch.h")
set(VWA_SIGNALSMITH_LINEAR_DIR "" CACHE PATH "Include directory containing signalsmith-linear/stft.h")

option(VWA_BUILD_NATIVE_TESTS "Build and register native CTest targets" ON)

# --- Resolve miniaudio ---
if(VWA_MINIAUDIO_DIR)
    set(_vwa_miniaudio_dir "${VWA_MINIAUDIO_DIR}")
else()
    FetchContent_Declare(
        miniaudio
        GIT_REPOSITORY https://github.com/mackron/miniaudio.git
        GIT_TAG ${VWA_MINIAUDIO_COMMIT}
        GIT_SHALLOW TRUE
    )
    FetchContent_MakeAvailable(miniaudio)
    set(_vwa_miniaudio_dir "${miniaudio_SOURCE_DIR}")
endif()

set(_vwa_miniaudio_header "${_vwa_miniaudio_dir}/miniaudio.h")
if(NOT EXISTS "${_vwa_miniaudio_header}")
    message(FATAL_ERROR "Pinned miniaudio.h not found at: ${_vwa_miniaudio_header}")
endif()
file(SHA256 "${_vwa_miniaudio_header}" _vwa_miniaudio_actual_sha256)
string(TOUPPER "${_vwa_miniaudio_actual_sha256}" _vwa_miniaudio_actual_sha256)
if(NOT _vwa_miniaudio_actual_sha256 STREQUAL "${VWA_MINIAUDIO_HEADER_SHA256}")
    message(FATAL_ERROR
        "miniaudio.h SHA-256 mismatch!\n"
        "  Expected: ${VWA_MINIAUDIO_HEADER_SHA256}\n"
        "  Actual:   ${_vwa_miniaudio_actual_sha256}\n"
        "  Path:     ${_vwa_miniaudio_header}"
    )
endif()

# --- Resolve signalsmith-stretch ---
if(VWA_SIGNALSMITH_STRETCH_DIR)
    set(_vwa_stretch_dir "${VWA_SIGNALSMITH_STRETCH_DIR}")
else()
    FetchContent_Declare(
        signalsmith_stretch
        GIT_REPOSITORY https://github.com/Signalsmith-Audio/signalsmith-stretch.git
        GIT_TAG ${VWA_SIGNALSMITH_STRETCH_COMMIT}
        GIT_SHALLOW TRUE
    )
    FetchContent_MakeAvailable(signalsmith_stretch)
    set(_vwa_stretch_dir "${signalsmith_stretch_SOURCE_DIR}")
endif()

if(NOT EXISTS "${_vwa_stretch_dir}/signalsmith-stretch.h")
    message(FATAL_ERROR "signalsmith-stretch.h not found in: ${_vwa_stretch_dir}")
endif()

# --- Resolve signalsmith-linear ---
if(VWA_SIGNALSMITH_LINEAR_DIR)
    set(_vwa_linear_dir "${VWA_SIGNALSMITH_LINEAR_DIR}")
else()
    FetchContent_Declare(
        signalsmith_linear
        GIT_REPOSITORY https://github.com/Signalsmith-Audio/linear.git
        GIT_TAG ${VWA_SIGNALSMITH_LINEAR_COMMIT}
        GIT_SHALLOW TRUE
    )
    FetchContent_MakeAvailable(signalsmith_linear)
    set(_vwa_linear_dir "${signalsmith_linear_SOURCE_DIR}/include")
endif()

if(NOT EXISTS "${_vwa_linear_dir}/signalsmith-linear/stft.h")
    message(FATAL_ERROR "signalsmith-linear/stft.h not found in: ${_vwa_linear_dir}")
endif()

# --- Target 1: vwa_native_output.dll ---
add_library(vwa_native_output SHARED
    vwa_native_output.c
    vwa_native_output.h
)
set_target_properties(vwa_native_output PROPERTIES
    C_STANDARD 11
    C_STANDARD_REQUIRED ON
    C_EXTENSIONS OFF
    PREFIX ""
)
target_compile_definitions(vwa_native_output PRIVATE VWA_OUTPUT_BUILD_DLL)
target_include_directories(vwa_native_output PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}"
    "${_vwa_miniaudio_dir}"
)
if(MINGW)
    target_compile_options(vwa_native_output PRIVATE -O2 -Wall -Wextra -Wpedantic -Wno-unused-function)
    target_link_options(vwa_native_output PRIVATE -static-libgcc -Wl,--strip-all)
elseif(MSVC)
    target_compile_options(vwa_native_output PRIVATE /O2 /W3)
endif()
if(WIN32)
    target_link_libraries(vwa_native_output PRIVATE ole32 uuid user32)
endif()

# --- Target 2: vwa_limiter.dll ---
add_library(vwa_limiter SHARED
    vwa_limiter.c
    vwa_limiter.h
)
set_target_properties(vwa_limiter PROPERTIES
    C_STANDARD 11
    C_STANDARD_REQUIRED ON
    C_EXTENSIONS OFF
    PREFIX ""
)
target_compile_definitions(vwa_limiter PRIVATE VWA_LIMITER_BUILD_DLL)
target_include_directories(vwa_limiter PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
if(MINGW)
    target_compile_options(vwa_limiter PRIVATE -O2 -Wall -Wextra -Wpedantic -Wno-unused-function)
    target_link_options(vwa_limiter PRIVATE -static-libgcc -Wl,--strip-all)
elseif(MSVC)
    target_compile_options(vwa_limiter PRIVATE /O2 /W3)
endif()

# --- Target 3: vwa_chorus_pitch.dll ---
add_library(vwa_chorus_pitch SHARED
    vwa_chorus_pitch.cpp
)
set_target_properties(vwa_chorus_pitch PROPERTIES
    CXX_STANDARD 17
    CXX_STANDARD_REQUIRED ON
    CXX_EXTENSIONS OFF
    PREFIX ""
)
target_include_directories(vwa_chorus_pitch PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}"
    "${_vwa_stretch_dir}"
    "${_vwa_linear_dir}"
)
if(MINGW)
    target_compile_options(vwa_chorus_pitch PRIVATE -O3 -Wall -Wextra)
    target_link_options(vwa_chorus_pitch PRIVATE -static -Wl,--strip-all)
elseif(MSVC)
    target_compile_options(vwa_chorus_pitch PRIVATE /O2 /W3)
endif()

# --- Target 4: vwa_native_output_test.exe (CTest) ---
if(VWA_BUILD_NATIVE_TESTS AND BUILD_TESTING)
    add_executable(vwa_native_output_test
        vwa_native_output.c
        test_vwa_output.c
    )
    set_target_properties(vwa_native_output_test PROPERTIES
        C_STANDARD 11
        C_STANDARD_REQUIRED ON
        C_EXTENSIONS OFF
    )
    target_include_directories(vwa_native_output_test PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}"
        "${_vwa_miniaudio_dir}"
    )
    if(MINGW)
        target_compile_options(vwa_native_output_test PRIVATE -O2 -Wall -Wextra -Wpedantic -Wno-unused-function)
        target_link_options(vwa_native_output_test PRIVATE -static-libgcc -Wl,--strip-all)
    elseif(MSVC)
        target_compile_options(vwa_native_output_test PRIVATE /O2 /W3)
    endif()
    if(WIN32)
        target_link_libraries(vwa_native_output_test PRIVATE ole32 uuid user32)
    endif()

    add_test(
        NAME vwa_native_output_null_device
        COMMAND vwa_native_output_test --null-json
    )
endif()
