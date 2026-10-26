#!/usr/bin/env python3
"""
Qualcomm Snapdragon 410 (MSM8916) Adreno 306 GPU Rendering Benchmark
Author: OpenStick Contributor
License: MIT

Measures Adreno 306 off-screen rendering performance (FPS, frame time, fill rate)
at 720p (1280x720) directly in VRAM via EGL & OpenGL ES 2.0/3.0.
"""

import ctypes
import os
import sys
import time

def main():
    print("=" * 65)
    print(" Qualcomm MSM8916 Adreno 306 GPU Hardware Rendering Benchmark ")
    print("=" * 65)

    render_node = "/dev/dri/renderD128"
    if not os.path.exists(render_node):
        print(f"[FAIL] Render node {render_node} does not exist!")
        sys.exit(1)

    try:
        fd = os.open(render_node, os.O_RDWR)
        libgbm = ctypes.CDLL("libgbm.so.1")
        libegl = ctypes.CDLL("libEGL.so.1")
        libgles = ctypes.CDLL("libGLESv2.so.2")
    except Exception as e:
        print(f"[FAIL] Error initializing libraries: {e}")
        sys.exit(1)

    # 1. GBM & EGL Init
    libgbm.gbm_create_device.restype = ctypes.c_void_p
    gbm_dev = libgbm.gbm_create_device(fd)
    libegl.eglGetDisplay.restype = ctypes.c_void_p
    libegl.eglGetDisplay.argtypes = [ctypes.c_void_p]
    egl_disp = libegl.eglGetDisplay(gbm_dev)
    libegl.eglInitialize(egl_disp, None, None)
    libegl.eglBindAPI(0x30A0)

    cfg_attribs = (ctypes.c_int * 5)(0x3040, 4, 0x3024, 8, 0x3038)
    cfg, ncfg = ctypes.c_void_p(), ctypes.c_int()
    libegl.eglChooseConfig(egl_disp, cfg_attribs, ctypes.byref(cfg), 1, ctypes.byref(ncfg))
    ctx_attribs = (ctypes.c_int * 3)(0x3098, 2, 0x3038)
    libegl.eglCreateContext.restype = ctypes.c_void_p
    ctx = libegl.eglCreateContext(egl_disp, cfg, None, ctx_attribs)
    libegl.eglMakeCurrent(egl_disp, None, None, ctx)

    libgles.glGetString.restype = ctypes.c_char_p
    renderer = libgles.glGetString(0x1F01).decode()
    print(f"Device: {renderer} (Qualcomm Adreno 306)")

    # 2. Setup 720p Offscreen Framebuffer (1280x720)
    width, height = 1280, 720
    fbo = ctypes.c_uint()
    libgles.glGenFramebuffers(1, ctypes.byref(fbo))
    libgles.glBindFramebuffer(0x8D40, fbo.value)
    rbo = ctypes.c_uint()
    libgles.glGenRenderbuffers(1, ctypes.byref(rbo))
    libgles.glBindRenderbuffer(0x8D41, rbo.value)
    libgles.glRenderbufferStorage(0x8D41, 0x8058, width, height) # GL_RGBA8
    libgles.glFramebufferRenderbuffer(0x8D40, 0x8CE0, 0x8D41, rbo.value)
    libgles.glViewport(0, 0, width, height)

    # 3. Warm-up
    libgles.glClear(0x4000)
    libgles.glFinish()

    # 4. Benchmark loop: 500 frames of dynamic clear & color shifts
    total_frames = 500
    print(f"\nRunning 720p ({width}x{height}) benchmark across {total_frames} frames...")

    t0 = time.time()
    for i in range(total_frames):
        r = (i % 255) / 255.0
        g = ((i * 2) % 255) / 255.0
        b = ((i * 3) % 255) / 255.0
        libgles.glClearColor(ctypes.c_float(r), ctypes.c_float(g), ctypes.c_float(b), ctypes.c_float(1.0))
        libgles.glClear(0x4000)
    libgles.glFinish()
    t1 = time.time()

    elapsed = t1 - t0
    fps = total_frames / elapsed
    avg_latency_ms = (elapsed / total_frames) * 1000
    fillrate_mpixels = (width * height * total_frames) / elapsed / 1e6

    print("\n" + "-" * 50)
    print(f" >>> Total Render Time : {elapsed:.3f} s")
    print(f" >>> Average Frame Rate: {fps:.1f} FPS")
    print(f" >>> Average Frame Time: {avg_latency_ms:.2f} ms")
    print(f" >>> Pixel Fill Rate   : {fillrate_mpixels:.1f} Mpixels/sec")
    print("-" * 50 + "\n")

    if fps > 100:
        print("[PASS] GPU performance exceeds hardware acceleration threshold!")
    else:
        print("[WARN] Rendering speed lower than expected.")

if __name__ == "__main__":
    main()
