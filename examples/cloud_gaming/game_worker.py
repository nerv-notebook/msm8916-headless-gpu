#!/usr/bin/env python3
"""
Dedicated Cloud Game Worker Process for RetroStick
Runs an isolated Libretro core + Adreno 306 GPU offscreen rendering pipeline on /dev/dri/renderD128.
"""

import os
import sys
import time
import json
import argparse
import ctypes
import threading
import io
import socket
from http import HTTPStatus
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from PIL import Image, ImageDraw

CORE_PATHS = {
    "nes": "/usr/lib/aarch64-linux-gnu/libretro/nestopia_libretro.so",
    "gba": "/usr/lib/aarch64-linux-gnu/libretro/mgba_libretro.so"
}

JOYPAD_MAP = {
    "B": 0, "Y": 1, "SELECT": 2, "START": 3,
    "UP": 4, "DOWN": 5, "LEFT": 6, "RIGHT": 7,
    "A": 8, "X": 9, "L": 10, "R": 11,
}

class RetroGameInfo(ctypes.Structure):
    _fields_ = [
        ('path', ctypes.c_char_p),
        ('data', ctypes.c_void_p),
        ('size', ctypes.c_size_t),
        ('meta', ctypes.c_char_p)
    ]

class GameWorkerEngine:
    def __init__(self, rom_path, system="nes", title="", render_node="/dev/dri/renderD128"):
        self.rom_path = rom_path
        self.system = system
        self.title = title or os.path.basename(rom_path)
        self.render_node = render_node
        
        self.running = False
        self.stop_requested = False
        self.buttons = {btn_id: 0 for btn_id in range(16)}
        self.button_hold_until = {}
        self.latest_jpeg = None
        self.new_frame_event = threading.Condition()
        self.lock = threading.Lock()
        self.fast_forward = False
        self.fps = 0.0
        self.cpu_temp = "45°C"
        self.active_viewers = 0
        self.last_active_time = time.time()
        self.udp_sock = None
        self.udp_thread = None
        
        self._init_egl()

    def _init_egl(self):
        fd = os.open(self.render_node, os.O_RDWR)
        self.libgbm = ctypes.CDLL("libgbm.so.1")
        self.libegl = ctypes.CDLL("libEGL.so.1")
        self.libgles = ctypes.CDLL("libGLESv2.so.2")

        self.libgbm.gbm_create_device.restype = ctypes.c_void_p
        self.gbm_dev = self.libgbm.gbm_create_device(fd)

        self.libegl.eglGetDisplay.restype = ctypes.c_void_p
        self.libegl.eglGetDisplay.argtypes = [ctypes.c_void_p]
        self.egl_disp = self.libegl.eglGetDisplay(self.gbm_dev)
        self.libegl.eglInitialize(self.egl_disp, None, None)
        self.libegl.eglBindAPI(0x30A0)

        cfg_attribs = (ctypes.c_int * 5)(0x3040, 4, 0x3024, 8, 0x3038)
        cfg, ncfg = ctypes.c_void_p(), ctypes.c_int()
        self.libegl.eglChooseConfig(self.egl_disp, cfg_attribs, ctypes.byref(cfg), 1, ctypes.byref(ncfg))
        ctx_attribs = (ctypes.c_int * 3)(0x3098, 3, 0x3038)
        self.libegl.eglCreateContext.restype = ctypes.c_void_p
        self.libegl.eglCreateContext.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        self.ctx = self.libegl.eglCreateContext(self.egl_disp, cfg, None, ctx_attribs)

        self.libegl.eglMakeCurrent.restype = ctypes.c_bool
        self.libegl.eglMakeCurrent.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]

        # 64-bit safe GL argtypes
        self.libgles.glTexImage2D.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
        self.libgles.glReadPixels.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
        self.libgles.glBindTexture.argtypes = [ctypes.c_uint, ctypes.c_uint]
        self.libgles.glViewport.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self.libgles.glPixelStorei.argtypes = [ctypes.c_uint, ctypes.c_int]
        self.libgles.glVertexAttribPointer.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint, ctypes.c_ubyte, ctypes.c_int, ctypes.c_void_p]
        self.libgles.glGetString.restype = ctypes.c_char_p

    def set_button(self, btn_name, state):
        btn_upper = btn_name.upper()
        if btn_upper in ("FASTFORWARD", "FF", "SPEED"):
            with self.lock:
                self.fast_forward = bool(state)
            self.last_active_time = time.time()
            return True
        if btn_upper in JOYPAD_MAP:
            btn_id = JOYPAD_MAP[btn_upper]
            now = time.time()
            with self.lock:
                self.buttons[btn_id] = 1 if state else 0
                if state:
                    self.button_hold_until[btn_id] = now + 0.30
                else:
                    self.button_hold_until[btn_id] = 0
            self.last_active_time = now
            return True
        return False

    def start(self):
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def start_udp_listener(self, port):
        """Binds low-latency UDP socket on 127.0.0.1:port for microsecond input IPC."""
        try:
            self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp_sock.bind(("127.0.0.1", port))
            self.udp_sock.settimeout(0.5)
            self.udp_thread = threading.Thread(target=self._udp_loop, daemon=True)
            self.udp_thread.start()
            print(f"[Worker] Ultra-fast UDP input listener active on 127.0.0.1:{port}")
        except Exception as e:
            print(f"[Worker] UDP input bind error on {port}: {e}", file=sys.stderr)

    def _udp_loop(self):
        while not self.stop_requested:
            try:
                data, _ = self.udp_sock.recvfrom(256)
                if not data:
                    continue
                text = data.decode("ascii", errors="ignore").strip()
                if text.startswith("SYNC:"):
                    # Full idempotent controller state synchronization
                    raw_names = text[5:].split(",") if len(text) > 5 else []
                    active_set = set(a.strip().upper() for a in raw_names if a.strip())
                    now = time.time()
                    with self.lock:
                        for name, bid in JOYPAD_MAP.items():
                            is_active = name in active_set
                            self.buttons[bid] = 1 if is_active else 0
                            if is_active:
                                self.button_hold_until[bid] = now + 0.30
                            else:
                                self.button_hold_until[bid] = 0
                        self.fast_forward = bool("FASTFORWARD" in active_set or "FF" in active_set or "SPEED" in active_set)
                    self.last_active_time = now
                elif ":" in text:
                    btn, state_str = text.split(":", 1)
                    state = 1 if state_str.strip() in ("1", "true", "True") else 0
                    self.set_button(btn, state)
            except socket.timeout:
                continue
            except Exception:
                pass

    def stop(self):
        self.stop_requested = True
        if self.udp_sock:
            try:
                self.udp_sock.close()
            except Exception:
                pass
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        self.running = False

    def _setup_gl(self, out_w, out_h):
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
                col = vec3(texel.b, texel.g, texel.r);
                float scanline = sin(v_uv.y * 480.0 * 3.14159) * 0.5 + 0.5;
                col *= (1.0 - 0.12 * (1.0 - scanline));
            } else {
                col = texel.rgb;
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
        self.libgles.glVertexAttribPointer(a_pos, 2, 0x1406, 0, 16, ctypes.c_void_p(0))
        self.libgles.glEnableVertexAttribArray(a_uv)
        self.libgles.glVertexAttribPointer(a_uv, 2, 0x1406, 0, 16, ctypes.c_void_p(8))
        self.libgles.glUniform1i(self.u_tex, 0)

        self.game_tex = ctypes.c_uint()
        self.libgles.glGenTextures(1, ctypes.byref(self.game_tex))
        self.libgles.glBindTexture(0x0DE1, self.game_tex.value)
        self.libgles.glTexParameteri(0x0DE1, 0x2800, 0x2600)
        self.libgles.glTexParameteri(0x0DE1, 0x2801, 0x2600)
        self.libgles.glTexParameteri(0x0DE1, 0x2802, 0x812F) # GL_CLAMP_TO_EDGE
        self.libgles.glTexParameteri(0x0DE1, 0x2803, 0x812F) # GL_CLAMP_TO_EDGE

        # FBO
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
        self.running = True
        is_nes = (self.system == "nes")
        out_w, out_h = (256, 240) if is_nes else (240, 160)

        # Bind context in this worker thread
        ret = self.libegl.eglMakeCurrent(self.egl_disp, None, None, self.ctx)
        if not ret:
            print(f"[Worker] eglMakeCurrent failed: {hex(self.libegl.eglGetError())}", file=sys.stderr)
            self.running = False
            return

        self._setup_gl(out_w, out_h)
        self.libgles.glUniform1i(self.u_is_nes, 1 if is_nes else 0)

        core_lib = CORE_PATHS[self.system]
        core = ctypes.CDLL(core_lib)

        TMP_DIR_PTR = ctypes.c_char_p(b"/tmp\0")
        latest_frame = {}
        def env_cb(cmd, data):
            if cmd in (9, 31):
                if data:
                    p = ctypes.cast(data, ctypes.POINTER(ctypes.c_char_p))
                    p.contents = TMP_DIR_PTR
                return True
            if cmd == 10:
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
            if port != 0:
                return 0
            now = time.time()
            with self.lock:
                if self.buttons.get(id_, 0) and now < self.button_hold_until.get(id_, 0):
                    return 1
                return 0

        self.cb_env = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_uint, ctypes.c_void_p)(env_cb)
        self.cb_video = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_size_t)(video_cb)
        self.cb_audio = ctypes.CFUNCTYPE(None, ctypes.c_int16, ctypes.c_int16)(audio_cb)
        self.cb_audio_batch = ctypes.CFUNCTYPE(ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t)(audio_batch_cb)
        self.cb_poll = ctypes.CFUNCTYPE(None)(input_poll_cb)
        self.cb_input = ctypes.CFUNCTYPE(ctypes.c_int16, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint)(input_state_cb)

        core.retro_set_environment(self.cb_env)
        core.retro_set_video_refresh(self.cb_video)
        core.retro_set_audio_sample(self.cb_audio)
        core.retro_set_audio_sample_batch(self.cb_audio_batch)
        core.retro_set_input_poll(self.cb_poll)
        core.retro_set_input_state(self.cb_input)
        core.retro_init()

        with open(self.rom_path, "rb") as f:
            rom_bytes = f.read()

        buf = (ctypes.c_ubyte * len(rom_bytes)).from_buffer_copy(rom_bytes)
        game = RetroGameInfo(self.rom_path.encode('utf-8'), ctypes.cast(buf, ctypes.c_void_p), len(rom_bytes), None)
        core.retro_load_game.restype = ctypes.c_bool
        core.retro_load_game.argtypes = [ctypes.POINTER(RetroGameInfo)]
        if not core.retro_load_game(ctypes.byref(game)):
            print("[Worker] Failed to load ROM!", file=sys.stderr)
            self.running = False
            return

        read_buf = (ctypes.c_ubyte * (out_w * out_h * 4))()
        target_fps = 30.0
        frame_interval = 1.0 / target_fps
        fps_counter = 0
        last_fps_time = time.time()
        hud_counter = 0
        loop_start_time = time.time()
        auto_start_done = False
        emulated_frames = 0
        cached_hud_img = None
        cached_hud_key = None

        try:
            while not self.stop_requested:
                frame_start = time.time()
                is_ff = getattr(self, "fast_forward", False)
                ticks = 4 if is_ff else 2

                # Frame-accurate auto-start pulse at exactly frame 120 (Title screen ready)
                if is_nes and not auto_start_done and (emulated_frames >= 120):
                    with self.lock:
                        self.buttons[3] = 1
                        self.button_hold_until[3] = time.time() + 0.35
                    auto_start_done = True
                    print(f"[Worker] Auto-pulsed START at frame {emulated_frames} into World 1-1", flush=True)

                for _ in range(ticks):
                    core.retro_run()
                    emulated_frames += 1

                if 'data' in latest_frame:
                    w = latest_frame['w']
                    h = latest_frame['h']
                    pitch = latest_frame.get('pitch', w * 4 if is_nes else w * 2)

                    self.libgles.glBindTexture(0x0DE1, self.game_tex.value)
                    if is_nes:
                        self.libgles.glPixelStorei(0x0CF2, pitch // 4)
                        self.libgles.glTexImage2D(0x0DE1, 0, 0x1908, w, h, 0, 0x1908, 0x1401, latest_frame['data'])
                    else:
                        self.libgles.glPixelStorei(0x0CF2, pitch // 2)
                        self.libgles.glTexImage2D(0x0DE1, 0, 0x1907, w, h, 0, 0x1907, 0x8363, latest_frame['data'])

                    self.libgles.glPixelStorei(0x0CF2, 0)
                    self.libgles.glDrawArrays(0x0005, 0, 4)

                self.libgles.glReadPixels(0, 0, out_w, out_h, 0x1908, 0x1401, ctypes.byref(read_buf))
                img = Image.frombuffer("RGBA", (out_w, out_h), read_buf, "raw", "RGBA", 0, 1)
                img = img.transpose(Image.FLIP_TOP_BOTTOM)

                hud_counter += 1
                if hud_counter % 30 == 0:
                    try:
                        with open("/sys/class/thermal/thermal_zone0/temp", "r") as tf:
                            self.cpu_temp = f"{int(tf.read().strip()) // 1000}°C"
                    except Exception:
                        pass

                # Ultra-fast cached HUD overlay (paste takes 0.4ms vs 64ms for draw.text)
                hud_key = (round(self.fps), is_ff, self.cpu_temp)
                if cached_hud_img is None or hud_key != cached_hud_key:
                    cached_hud_key = hud_key
                    cached_hud_img = Image.new("RGBA", (out_w, 14), (10, 15, 25, 220))
                    d = ImageDraw.Draw(cached_hud_img)
                    speed_str = " ⏩2x" if is_ff else ""
                    hud_text = f"MSM8916 CLOUD GPU | {self.fps:.0f} FPS{speed_str} | {self.cpu_temp}"
                    d.text((4, 1), hud_text, fill=(0, 242, 254))

                img.paste(cached_hud_img, (0, 0), cached_hud_img)

                bio = io.BytesIO()
                img.convert("RGB").save(bio, format="JPEG", quality=78, subsampling=0)
                jpeg_data = bio.getvalue()

                with self.new_frame_event:
                    self.latest_jpeg = jpeg_data
                    self.new_frame_event.notify_all()

                fps_counter += 1
                now = time.time()
                if now - last_fps_time >= 1.0:
                    self.fps = fps_counter / (now - last_fps_time)
                    fps_counter = 0
                    last_fps_time = now

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
            try:
                self.libegl.eglMakeCurrent(self.egl_disp, None, None, None)
                self.libegl.eglDestroyContext(self.egl_disp, self.ctx)
            except Exception:
                pass
            self.running = False
            self.latest_jpeg = None
            print(f"[Worker] Cleanly stopped {self.title}.")

    def mjpeg_generator(self):
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


class WorkerHTTPHandler(BaseHTTPRequestHandler):
    engine = None

    def log_message(self, format, *args):
        pass # Silence logging for max speed

    def do_GET(self):
        if self.path.startswith("/stream"):
            if not self.engine or not self.engine.running:
                self.send_error(HTTPStatus.NOT_FOUND, "Not running")
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache, private, no-store")
            self.send_header("Pragma", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                for chunk in self.engine.mjpeg_generator():
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (ConnectionResetError, BrokenPipeError):
                pass
        elif self.path == "/status":
            st = {
                "running": self.engine.running if self.engine else False,
                "fps": round(self.engine.fps, 1) if self.engine else 0.0,
                "title": self.engine.title if self.engine else "",
                "cpu_temp": self.engine.cpu_temp if self.engine else "--°C"
            }
            body = json.dumps(st).encode()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if self.path == "/input":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode()) if length > 0 else {}
            btn = data.get("btn", "")
            state = int(data.get("state", 0))
            if self.engine:
                self.engine.set_button(btn, state)
            body = b'{"status":"ok"}'
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/stop":
            if self.engine:
                self.engine.stop()
            body = b'{"status":"stopped"}'
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            # Exit process shortly
            threading.Thread(target=lambda: (time.sleep(0.5), os._exit(0))).start()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)


class ReusableThreadingServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--rom", type=str, required=True)
    parser.add_argument("--system", type=str, default="nes")
    parser.add_argument("--title", type=str, default="")
    args = parser.parse_args()

    engine = GameWorkerEngine(args.rom, system=args.system, title=args.title)
    engine.start()
    engine.start_udp_listener(args.port)

    WorkerHTTPHandler.engine = engine
    server = ReusableThreadingServer(("127.0.0.1", args.port), WorkerHTTPHandler)
    print(f"[Worker] Online on 127.0.0.1:{args.port} for {args.title} ({args.system})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        engine.stop()

if __name__ == "__main__":
    main()
