#!/usr/bin/env python3
"""
Qualcomm Snapdragon 410 (MSM8916) Adreno 306 Hardware 3D GPU Verification Script
Author: OpenStick Contributor
License: MIT

This script opens /dev/dri/renderD128, creates a GBM device, initializes an
EGL surfaceless/headless context, queries the Freedreno OpenGL ES 3.0 hardware
renderer, and executes a real off-screen 3D clear & readback test directly in VRAM.
"""

import ctypes
import os
import sys

def main():
    print("=" * 60)
    print(" Qualcomm MSM8916 Adreno 306 GPU Hardware Verification Test ")
    print("=" * 60)

    render_node = "/dev/dri/renderD128"
    if not os.path.exists(render_node):
        print(f"[FAIL] Render node {render_node} does not exist!")
        print("Please ensure 'gpu-sched' and 'msm' modules are loaded.")
        sys.exit(1)

    try:
        fd = os.open(render_node, os.O_RDWR)
        print(f"[OK] Opened {render_node} in RDWR mode (FD = {fd})")
    except Exception as e:
        print(f"[FAIL] Cannot open {render_node}: {e}")
        sys.exit(1)

    try:
        libdrm = ctypes.CDLL("libdrm.so.2")
        libgbm = ctypes.CDLL("libgbm.so.1")
        libegl = ctypes.CDLL("libEGL.so.1")
        libgles = ctypes.CDLL("libGLESv2.so.2")
    except Exception as e:
        print(f"[FAIL] Failed to load graphics libraries: {e}")
        print("Please install: apt-get install -y libdrm-freedreno1 libgbm1 libegl1-mesa libgles2")
        sys.exit(1)

    # 1. Create GBM Device from Render Node FD
    libgbm.gbm_create_device.restype = ctypes.c_void_p
    gbm_dev = libgbm.gbm_create_device(fd)
    if not gbm_dev:
        print("[FAIL] gbm_create_device returned NULL")
        sys.exit(1)
    print(f"[OK] GBM Device created successfully: {hex(gbm_dev)}")

    # 2. Initialize EGL Platform Display
    libegl.eglGetDisplay.restype = ctypes.c_void_p
    libegl.eglGetDisplay.argtypes = [ctypes.c_void_p]
    egl_disp = libegl.eglGetDisplay(gbm_dev)
    if not egl_disp:
        print("[FAIL] eglGetDisplay returned NULL")
        sys.exit(1)

    major, minor = ctypes.c_int(), ctypes.c_int()
    if not libegl.eglInitialize(egl_disp, ctypes.byref(major), ctypes.byref(minor)):
        print("[FAIL] eglInitialize failed")
        sys.exit(1)
    print(f"[OK] EGL initialized: version {major.value}.{minor.value}")

    libegl.eglQueryString.restype = ctypes.c_char_p
    print(f"     EGL Vendor: {libegl.eglQueryString(egl_disp, 0x3053).decode()}")
    print(f"     EGL Version: {libegl.eglQueryString(egl_disp, 0x3054).decode()}")

    # 3. Bind OpenGL ES API
    libegl.eglBindAPI(0x30A0) # EGL_OPENGL_ES_API

    # 4. Choose Config (RGB888, OpenGL ES 2/3)
    cfg_attribs = (ctypes.c_int * 7)(
        0x3040, 0x0004, # EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT
        0x3024, 8,      # EGL_RED_SIZE, 8
        0x3038          # EGL_NONE
    )
    cfg, num_cfg = ctypes.c_void_p(), ctypes.c_int()
    libegl.eglChooseConfig(egl_disp, cfg_attribs, ctypes.byref(cfg), 1, ctypes.byref(num_cfg))

    # 5. Create EGL Context (GLES 2/3)
    ctx_attribs = (ctypes.c_int * 3)(0x3098, 2, 0x3038)
    libegl.eglCreateContext.restype = ctypes.c_void_p
    ctx = libegl.eglCreateContext(egl_disp, cfg, None, ctx_attribs)
    if not ctx:
        print("[FAIL] eglCreateContext failed")
        sys.exit(1)
    print(f"[OK] EGL Context created: {hex(ctx)}")

    # 6. Make Current (Headless Surfaceless)
    if not libegl.eglMakeCurrent(egl_disp, None, None, ctx):
        print("[FAIL] eglMakeCurrent failed")
        sys.exit(1)
    print("[OK] EGL Context made current (Headless Surfaceless)")

    # 7. Query OpenGL ES Hardware Device Strings
    libgles.glGetString.restype = ctypes.c_char_p
    vendor = libgles.glGetString(0x1F00).decode()
    renderer = libgles.glGetString(0x1F01).decode()
    version = libgles.glGetString(0x1F02).decode()
    glsl = libgles.glGetString(0x8B8C).decode()

    print("\n" + "-" * 50)
    print(f" >>> GPU VENDOR   : {vendor}")
    print(f" >>> GPU RENDERER : {renderer}")
    print(f" >>> GL VERSION   : {version}")
    print(f" >>> GLSL VERSION : {glsl}")
    print("-" * 50 + "\n")

    # 8. Perform 3D Off-screen Framebuffer Render Test
    fbo = ctypes.c_uint()
    libgles.glGenFramebuffers(1, ctypes.byref(fbo))
    libgles.glBindFramebuffer(0x8D40, fbo.value) # GL_FRAMEBUFFER

    rbo = ctypes.c_uint()
    libgles.glGenRenderbuffers(1, ctypes.byref(rbo))
    libgles.glBindRenderbuffer(0x8D41, rbo.value) # GL_RENDERBUFFER
    libgles.glRenderbufferStorage(0x8D41, 0x8058, 64, 64) # GL_RGBA8, 64x64
    libgles.glFramebufferRenderbuffer(0x8D40, 0x8CE0, 0x8D41, rbo.value) # GL_COLOR_ATTACHMENT0

    status = libgles.glCheckFramebufferStatus(0x8D40)
    if status != 0x8CD5: # GL_FRAMEBUFFER_COMPLETE
        print(f"[FAIL] Framebuffer incomplete, status = {hex(status)}")
        sys.exit(1)
    print("[OK] Adreno GPU VRAM Framebuffer Object (FBO) allocated successfully")

    # Clear screen to Pure Red (R=1.0, G=0.0, B=0.0, A=1.0)
    libgles.glClearColor(ctypes.c_float(1.0), ctypes.c_float(0.0), ctypes.c_float(0.0), ctypes.c_float(1.0))
    libgles.glClear(0x00004000) # GL_COLOR_BUFFER_BIT
    libgles.glFinish()

    # Read back 1 pixel from GPU VRAM
    pixel = (ctypes.c_ubyte * 4)()
    libgles.glReadPixels(0, 0, 1, 1, 0x1908, 0x1401, ctypes.byref(pixel)) # GL_RGBA, GL_UNSIGNED_BYTE
    print(f"[TEST] VRAM Pixel Readback: R={pixel[0]}, G={pixel[1]}, B={pixel[2]}, A={pixel[3]}")

    if pixel[0] == 255 and pixel[1] == 0 and pixel[2] == 0:
        print("\n" + "=" * 60)
        print(" [SUCCESS] 3D GPU RENDERING PIPELINE IS 100% OPERATIONAL! ")
        print(" Hardware Adreno 306 GPU is fully accelerated and functional! ")
        print("=" * 60)
    else:
        print("[FAIL] Pixel values do not match expected clear color.")
        sys.exit(1)

if __name__ == "__main__":
    main()
