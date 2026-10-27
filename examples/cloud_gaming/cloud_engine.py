#!/usr/bin/env python3
"""
Qualcomm MSM8916 (Snapdragon 410) Adreno 306 Cloud Gaming Engine
Direct hardware-accelerated Libretro runner utilizing Mesa Freedreno EGL/GLES
on /dev/dri/renderD128 and Cortex-A53 4-Core CPU.
"""

import os
import sys
import time
import json
import ctypes
import threading
import io
from PIL import Image, ImageDraw, ImageFont

# Libretro structs
class RetroGameInfo(ctypes.Structure):
    _fields_ = [
        ('path', ctypes.c_char_p),
        ('data', ctypes.c_void_p),
        ('size', ctypes.c_size_t),
        ('meta', ctypes.c_char_p)
    ]

class RetroGameGeometry(ctypes.Structure):
    _fields_ = [
        ('base_width', ctypes.c_uint),
        ('base_height', ctypes.c_uint),
        ('max_width', ctypes.c_uint),
        ('max_height', ctypes.c_uint),
        ('aspect_ratio', ctypes.c_float)
    ]

class RetroSystemTiming(ctypes.Structure):
    _fields_ = [
        ('fps', ctypes.c_double),
        ('sample_rate', ctypes.c_double)
    ]

class RetroSystemAVInfo(ctypes.Structure):
    _fields_ = [
        ('geometry', RetroGameGeometry),
        ('timing', RetroSystemTiming)
    ]

# Libretro Joypad Button IDs
JOYPAD_MAP = {
    "B": 0,
    "Y": 1,
    "SELECT": 2,
    "START": 3,
    "UP": 4,
    "DOWN": 5,
    "LEFT": 6,
    "RIGHT": 7,
    "A": 8,
    "X": 9,
    "L": 10,
    "R": 11,
}

CORE_PATHS = {
    "nes": "/usr/lib/aarch64-linux-gnu/libretro/nestopia_libretro.so",
    "gba": "/usr/lib/aarch64-linux-gnu/libretro/mgba_libretro.so"
}

class CloudGameEngine:
    def __init__(self, render_node="/dev/dri/renderD128"):
        self.render_node = render_node
        self.running = False
        self.paused = False
        self.current_rom = None
        self.current_system = None
        self.current_title = ""
        self.fps = 0.0
        self.frame_count = 0
        self.last_fps_time = time.time()
        self.cpu_temp = "45°C"
        
        # Gamepad states: {0: 0, 1: 0, ...}
        self.buttons = {btn_id: 0 for btn_id in range(16)}
        self.latest_jpeg = None
        self.new_frame_event = threading.Condition()
        self.lock = threading.Lock()
        
        self.egl_initialized = False
        self.core = None
        self.thread = None
        self.stop_requested = False
        self.active_viewers = 0
        self.last_active_time = time.time()
        
        # GPU Device String
        self.gpu_device_name = "Qualcomm Adreno 306"
        self.glsl_version = "OpenGL ES 3.0"
        
        # Pre-initialize EGL if available
        if os.path.exists(render_node):
            try:
                self._init_egl()
            except Exception as e:
                print(f"[CloudEngine] GPU Init warning: {e}", file=sys.stderr)

    def _init_egl(self):
        fd = os.open(self.render_node, os.O_RDWR)
        self.libgbm = ctypes.CDLL("libgbm.so.1")
        self.libegl = ctypes.CDLL("libEGL.so.1")
        self.libgles = ctypes.CDLL("libGLESv2.so.2")

        # 64-bit ctypes argtypes configuration
        self.libgbm.gbm_create_device.restype = ctypes.c_void_p
        self.gbm_dev = self.libgbm.gbm_create_device(fd)
        
        self.libegl.eglGetDisplay.restype = ctypes.c_void_p
        self.libegl.eglGetDisplay.argtypes = [ctypes.c_void_p]
        self.egl_disp = self.libegl.eglGetDisplay(self.gbm_dev)
        self.libegl.eglInitialize(self.egl_disp, None, None)
        self.libegl.eglBindAPI(0x30A0) # EGL_OPENGL_ES_API

        cfg_attribs = (ctypes.c_int * 5)(0x3040, 4, 0x3024, 8, 0x3038)
        cfg, ncfg = ctypes.c_void_p(), ctypes.c_int()
        self.libegl.eglChooseConfig(self.egl_disp, cfg_attribs, ctypes.byref(cfg), 1, ctypes.byref(ncfg))
        ctx_attribs = (ctypes.c_int * 3)(0x3098, 2, 0x3038)
        self.libegl.eglCreateContext.restype = ctypes.c_void_p
        self.ctx = self.libegl.eglCreateContext(self.egl_disp, cfg, None, ctx_attribs)
        self.libegl.eglMakeCurrent(self.egl_disp, None, None, self.ctx)

        # Typed GL calls for safety on aarch64
        self.libgles.glTexImage2D.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
        self.libgles.glReadPixels.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
        self.libgles.glBindTexture.argtypes = [ctypes.c_uint, ctypes.c_uint]
        self.libgles.glViewport.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self.libgles.glPixelStorei.argtypes = [ctypes.c_uint, ctypes.c_int]
        self.libgles.glGetString.restype = ctypes.c_char_p

        renderer = self.libgles.glGetString(0x1F01).decode(errors="ignore")
        self.gpu_device_name = f"{renderer} (Qualcomm Adreno 306)"
        print(f"[CloudEngine] GPU online: {self.gpu_device_name}")
        self.egl_initialized = True

    def set_button(self, btn_name, state):
        """Update virtual gamepad button state (1 for pressed, 0 for released)."""
        btn_upper = btn_name.upper()
        if btn_upper in JOYPAD_MAP:
            btn_id = JOYPAD_MAP[btn_upper]
            with self.lock:
                self.buttons[btn_id] = 1 if state else 0
            self.last_active_time = time.time()
            return True
        return False

    def reset_buttons(self):
        with self.lock:
            for k in self.buttons:
                self.buttons[k] = 0

    def start_game(self, rom_path, system="nes", title=""):
        """Start a hardware-accelerated cloud gaming session."""
        if self.running:
            self.stop_game()

        if system not in CORE_PATHS or not os.path.isfile(CORE_PATHS[system]):
            raise ValueError(f"Core for {system} not available on system.")

        if not os.path.isfile(rom_path):
            raise FileNotFoundError(f"ROM not found: {rom_path}")

        self.current_rom = rom_path
        self.current_system = system
        self.current_title = title or os.path.basename(rom_path)
        self.stop_requested = False
        self.reset_buttons()
        self.last_active_time = time.time()

        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        return True

    def stop_game(self):
        """Stop current cloud gaming session."""
        if not self.running:
            return
        self.stop_requested = True
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        self.running = False
        self.current_rom = None
        self.latest_jpeg = None
        self.reset_buttons()

    def get_status(self):
        # Update CPU Temp
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                self.cpu_temp = f"{int(f.read().strip()) // 1000}°C"
        except Exception:
            pass

        return {
            "running": self.running,
            "title": self.current_title,
            "system": self.current_system,
            "fps": round(self.fps, 1),
            "cpu_temp": self.cpu_temp,
            "gpu_device": self.gpu_device_name,
            "render_node": self.render_node,
            "active_viewers": self.active_viewers,
            "engine": "MSM8916 Headless Freedreno + Libretro"
        }

    def _setup_gl_pipeline(self, out_w, out_h):
        vs_src = b"""
        attribute vec2 a_pos;
        attribute vec2 a_uv;
        varying vec2 v_uv;
        void main() {
            gl_Position = vec4(a_pos, 0.0, 1.0);
            v_uv = a_uv;
        }
        """

        fs_src = b"""
        precision mediump float;
        varying vec2 v_uv;
        uniform sampler2D u_tex;
        uniform int u_is_nes;

        void main() {
            vec4 texel = texture2D(u_tex, v_uv);
            vec3 col;
            if (u_is_nes == 1) {
                // XRGB8888 little-endian BGR to RGB
                col = vec3(texel.b, texel.g, texel.r);
                // Subtle CRT Scanline
                float scanline = sin(v_uv.y * 480.0 * 3.14159) * 0.5 + 0.5;
                col *= (1.0 - 0.12 * (1.0 - scanline));
            } else {
                // GBA RGB565 direct
                col = texel.rgb;
                // Subtle LCD pixel grid
                float grid = (mod(gl_FragCoord.x, 2.0) < 1.0 ? 0.96 : 1.0) * (mod(gl_FragCoord.y, 2.0) < 1.0 ? 0.96 : 1.0);
                col *= grid;
            }
            gl_FragColor = vec4(col, 1.0);
        }
        """

        def compile_shader(shader_type, src):
            s = self.libgles.glCreateShader(shader_type)
            src_p = ctypes.c_char_p(src)
            self.libgles.glShaderSource(s, 1, ctypes.byref(src_p), None)
            self.libgles.glCompileShader(s)
            return s

        vs = compile_shader(0x8B31, vs_src)
        fs = compile_shader(0x8B30, fs_src)
        prog = self.libgles.glCreateProgram()
        self.libgles.glAttachShader(prog, vs)
        self.libgles.glAttachShader(prog, fs)
        self.libgles.glLinkProgram(prog)
        self.libgles.glUseProgram(prog)

        a_pos = self.libgles.glGetAttribLocation(prog, b"a_pos")
        a_uv = self.libgles.glGetAttribLocation(prog, b"a_uv")
        self.u_tex = self.libgles.glGetUniformLocation(prog, b"u_tex")
        self.u_is_nes = self.libgles.glGetUniformLocation(prog, b"u_is_nes")

        quad_data = (ctypes.c_float * 16)(
            -1.0, -1.0,  0.0, 1.0,
             1.0, -1.0,  1.0, 1.0,
            -1.0,  1.0,  0.0, 0.0,
             1.0,  1.0,  1.0, 0.0
        )
        vbo = ctypes.c_uint()
        self.libgles.glGenBuffers(1, ctypes.byref(vbo))
        self.libgles.glBindBuffer(0x8892, vbo.value)
        self.libgles.glBufferData(0x8892, ctypes.sizeof(quad_data), ctypes.byref(quad_data), 0x88E4)

        self.libgles.glEnableVertexAttribArray(a_pos)
        self.libgles.glVertexAttribPointer(a_pos, 2, 0x1406, 0, 16, 0)
        self.libgles.glEnableVertexAttribArray(a_uv)
        self.libgles.glVertexAttribPointer(a_uv, 2, 0x1406, 0, 16, 8)
        self.libgles.glUniform1i(self.u_tex, 0)

        self.game_tex = ctypes.c_uint()
        self.libgles.glGenTextures(1, ctypes.byref(self.game_tex))
        self.libgles.glBindTexture(0x0DE1, self.game_tex.value)
        self.libgles.glTexParameteri(0x0DE1, 0x2800, 0x2600)
        self.libgles.glTexParameteri(0x0DE1, 0x2801, 0x2600)

        # Offscreen FBO
        self.fbo = ctypes.c_uint()
        self.libgles.glGenFramebuffers(1, ctypes.byref(self.fbo))
        self.libgles.glBindFramebuffer(0x8D40, self.fbo.value)
        self.rbo = ctypes.c_uint()
        self.libgles.glGenRenderbuffers(1, ctypes.byref(self.rbo))
        self.libgles.glBindRenderbuffer(0x8D41, self.rbo.value)
        self.libgles.glRenderbufferStorage(0x8D41, 0x8058, out_w, out_h)
        self.libgles.glFramebufferRenderbuffer(0x8D40, 0x8CE0, 0x8D41, self.rbo.value)
        self.libgles.glViewport(0, 0, out_w, out_h)

    def _run_loop(self):
        """Dedicated Cloud Gaming thread."""
        self.running = True
        is_nes = (self.current_system == "nes")
        out_w, out_h = (512, 480) if is_nes else (480, 320)

        # Make sure EGL context is current in this thread
        self.libegl.eglMakeCurrent(self.egl_disp, None, None, self.ctx)
        self._setup_gl_pipeline(out_w, out_h)
        self.libgles.glUniform1i(self.u_is_nes, 1 if is_nes else 0)

        # Load Libretro Core
        core_lib = CORE_PATHS[self.current_system]
        core = ctypes.CDLL(core_lib)

        latest_frame = {}
        pixel_fmt = [1 if is_nes else 2]

        def env_cb(cmd, data):
            if cmd in (9, 31):
                p = ctypes.cast(data, ctypes.POINTER(ctypes.c_char_p))
                p.contents.value = b"/tmp"
                return True
            if cmd == 10:
                fmt = ctypes.cast(data, ctypes.POINTER(ctypes.c_uint)).contents.value
                pixel_fmt[0] = fmt
                return True
            return False

        def video_cb(data, width, height, pitch):
            if data:
                latest_frame['data'] = data
                latest_frame['w'] = width
                latest_frame['h'] = height
                latest_frame['pitch'] = pitch

        def audio_cb(l, r): pass
        def audio_batch_cb(d, f): return f
        def input_poll_cb(): pass
        def input_state_cb(port, dev, idx, id_):
            with self.lock:
                return self.buttons.get(id_, 0)

        ENV_CB = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_uint, ctypes.c_void_p)(env_cb)
        VIDEO_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_size_t)(video_cb)
        AUDIO_CB = ctypes.CFUNCTYPE(None, ctypes.c_int16, ctypes.c_int16)(audio_cb)
        AUDIO_BATCH_CB = ctypes.CFUNCTYPE(ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t)(audio_batch_cb)
        INPUT_POLL_CB = ctypes.CFUNCTYPE(None)(input_poll_cb)
        INPUT_STATE_CB = ctypes.CFUNCTYPE(ctypes.c_int16, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint)(input_state_cb)

        core.retro_set_environment(ENV_CB)
        core.retro_set_video_refresh(VIDEO_CB)
        core.retro_set_audio_sample(AUDIO_CB)
        core.retro_set_audio_sample_batch(AUDIO_BATCH_CB)
        core.retro_set_input_poll(INPUT_POLL_CB)
        core.retro_set_input_state(INPUT_STATE_CB)
        core.retro_init()

        with open(self.current_rom, "rb") as f:
            rom_bytes = f.read()

        buf = (ctypes.c_ubyte * len(rom_bytes)).from_buffer_copy(rom_bytes)
        game = RetroGameInfo(self.current_rom.encode('utf-8'), ctypes.cast(buf, ctypes.c_void_p), len(rom_bytes), None)
        core.retro_load_game.restype = ctypes.c_bool
        core.retro_load_game.argtypes = [ctypes.POINTER(RetroGameInfo)]
        if not core.retro_load_game(ctypes.byref(game)):
            print("[CloudEngine] Failed to load ROM!", file=sys.stderr)
            self.running = False
            return

        read_buf = (ctypes.c_ubyte * (out_w * out_h * 4))()
        
        target_fps = 60.0
        frame_interval = 1.0 / target_fps
        fps_frame_counter = 0
        last_fps_calc = time.time()
        hud_counter = 0

        try:
            while not self.stop_requested:
                frame_start = time.time()

                # If no viewers for > 30 seconds, pause emulation loop to save power
                if self.active_viewers == 0 and (time.time() - self.last_active_time) > 30.0:
                    time.sleep(0.1)
                    continue

                # Run 1 frame in Libretro core
                core.retro_run()

                # Upload to GPU and execute shader
                if 'data' in latest_frame:
                    w = latest_frame['w']
                    h = latest_frame['h']
                    pitch = latest_frame.get('pitch', w * 4)

                    self.libgles.glBindTexture(0x0DE1, self.game_tex.value)
                    if is_nes:
                        # 4 bytes per pixel XRGB8888
                        self.libgles.glPixelStorei(0x0CF2, pitch // 4)
                        self.libgles.glTexImage2D(0x0DE1, 0, 0x1908, w, h, 0, 0x1908, 0x1401, latest_frame['data'])
                    else:
                        # 2 bytes per pixel RGB565
                        self.libgles.glPixelStorei(0x0CF2, pitch // 2)
                        self.libgles.glTexImage2D(0x0DE1, 0, 0x1907, w, h, 0, 0x1907, 0x8363, latest_frame['data'])
                    
                    self.libgles.glPixelStorei(0x0CF2, 0)
                    self.libgles.glDrawArrays(0x0005, 0, 4)

                # Read back FBO
                self.libgles.glReadPixels(0, 0, out_w, out_h, 0x1908, 0x1401, ctypes.byref(read_buf))
                
                # Pillow NEON SIMD JPEG encode
                img = Image.frombuffer("RGBA", (out_w, out_h), read_buf, "raw", "RGBA", 0, 1)
                img = img.transpose(Image.FLIP_TOP_BOTTOM)

                # Burn subtle OSD HUD banner at top of screen
                hud_counter += 1
                if hud_counter % 30 == 0:
                    # Update temp every half-second
                    try:
                        with open("/sys/class/thermal/thermal_zone0/temp", "r") as tf:
                            self.cpu_temp = f"{int(tf.read().strip()) // 1000}°C"
                    except Exception:
                        pass

                draw = ImageDraw.Draw(img)
                hud_text = f"MSM8916 CLOUD GPU | Adreno 306 @ /dev/dri/renderD128 | {self.fps:.0f} FPS | {self.cpu_temp}"
                draw.rectangle([(0, 0), (out_w, 16)], fill=(10, 15, 25, 220))
                draw.text((6, 2), hud_text, fill=(0, 242, 254))

                bio = io.BytesIO()
                img.convert("RGB").save(bio, format="JPEG", quality=68)
                jpeg_data = bio.getvalue()

                with self.new_frame_event:
                    self.latest_jpeg = jpeg_data
                    self.new_frame_event.notify_all()

                fps_frame_counter += 1
                now = time.time()
                if now - last_fps_calc >= 1.0:
                    self.fps = fps_frame_counter / (now - last_fps_calc)
                    fps_frame_counter = 0
                    last_fps_calc = now

                # Sleep to maintain 60 FPS
                elapsed = time.time() - frame_start
                sleep_time = frame_interval - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

        finally:
            try:
                core.retro_unload_game()
                core.retro_deinit()
            except Exception:
                pass
            self.running = False
            self.current_rom = None
            self.latest_jpeg = None
            print("[CloudEngine] Emulation loop cleanly stopped.")

    def mjpeg_generator(self):
        """Yields MJPEG stream chunks for HTTP response."""
        self.active_viewers += 1
        self.last_active_time = time.time()
        try:
            while self.running and not self.stop_requested:
                with self.new_frame_event:
                    self.new_frame_event.wait(timeout=1.0)
                    frame = self.latest_jpeg
                if frame:
                    yield (b"--frame\r\n"
                           b"Content-Type: image/jpeg\r\n"
                           b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n" +
                           frame + b"\r\n")
        finally:
            self.active_viewers = max(0, self.active_viewers - 1)
