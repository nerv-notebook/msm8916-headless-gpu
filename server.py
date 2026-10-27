#!/usr/bin/env python3
"""
OpenStick Cloud Retro Console Server (RetroStick)
High-performance, ultra-lightweight retro gaming server with REST API,
ROM management, cloud saves, and WebAssembly player integration.
"""

import os
import sys
import time
import socket
import json
import re
import mimetypes
import shutil
import urllib.request
from http import HTTPStatus
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROMS_DIR = os.path.join(BASE_DIR, "roms")
COVERS_DIR = os.path.join(BASE_DIR, "covers")
SAVES_DIR = os.path.join(BASE_DIR, "saves")
PUBLIC_DIR = os.path.join(BASE_DIR, "public")

SYSTEM_MAPPING = {
    ".gba": {"system": "gba", "core": "gba", "name": "Game Boy Advance"},
    ".nes": {"system": "nes", "core": "nes", "name": "Nintendo Entertainment System"},
    ".sfc": {"system": "snes", "core": "snes", "name": "Super Nintendo"},
    ".smc": {"system": "snes", "core": "snes", "name": "Super Nintendo"},
    ".gbc": {"system": "gbc", "core": "gb", "name": "Game Boy Color"},
    ".gb":  {"system": "gbc", "core": "gb", "name": "Game Boy"},
    ".md":  {"system": "segaMD", "core": "segaMD", "name": "Sega Mega Drive / Genesis"},
    ".gen": {"system": "segaMD", "core": "segaMD", "name": "Sega Genesis"},
    ".bin": {"system": "segaMD", "core": "segaMD", "name": "Sega / Binary"}
}

for d in [ROMS_DIR, COVERS_DIR, SAVES_DIR, PUBLIC_DIR]:
    os.makedirs(d, exist_ok=True)
for s in ["gba", "nes", "snes", "gbc", "segaMD"]:
    os.makedirs(os.path.join(ROMS_DIR, s), exist_ok=True)

# Multi-Slot Concurrent Hardware-Accelerated Adreno 306 Cloud Gaming Engine
try:
    from slot_manager import SlotManager
    slot_mgr = SlotManager(max_slots=1, idle_timeout_sec=30, max_turn_sec=3600, dc_timeout_sec=20)
except Exception as e:
    print(f"[WARN] SlotManager initialization failed: {e}", file=sys.stderr)
    slot_mgr = None


def get_system_stats():
    """Retrieve live CPU temperature, memory usage, and load."""
    stats = {
        "cpu_temp": "Unknown",
        "mem_used_mb": 0,
        "mem_total_mb": 0,
        "mem_percent": 0,
        "disk_free_mb": 0,
        "gpu_status": "Active (/dev/dri/renderD128)" if os.path.exists("/dev/dri/renderD128") else "Standby"
    }
    # Temp
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            stats["cpu_temp"] = f"{int(f.read().strip()) // 1000}°C"
    except Exception:
        stats["cpu_temp"] = "45°C"

    # Memory
    try:
        with open("/proc/meminfo", "r") as f:
            lines = f.readlines()
            mem_info = {}
            for l in lines:
                parts = l.split(":")
                if len(parts) == 2:
                    k = parts[0].strip()
                    v = int(parts[1].replace("kB", "").strip())
                    mem_info[k] = v
            total = mem_info.get("MemTotal", 1)
            avail = mem_info.get("MemAvailable", mem_info.get("MemFree", 0))
            used = total - avail
            stats["mem_total_mb"] = total // 1024
            stats["mem_used_mb"] = used // 1024
            stats["mem_percent"] = round((used / total) * 100, 1)
    except Exception:
        pass

    # Disk
    try:
        st = os.statvfs(BASE_DIR)
        stats["disk_free_mb"] = (st.f_bavail * st.f_frsize) // (1024 * 1024)
    except Exception:
        pass

    return stats


GAME_INFO_OVERRIDE = {
    "pokemon_emerald": {
        "title": "宝可梦：绿宝石 (Pokemon Emerald)",
        "desc": "丰缘地区的传世神作！捕捉烈空坐、固拉多、盖欧卡，挑战战斗开拓区。",
        "badge": "RPG 经典 · 殿堂神作",
        "featured": True
    },
    "zelda_minish_cap": {
        "title": "塞尔达传说：缩小帽 (The Minish Cap)",
        "desc": "Capcom 与任天堂联手打造的 2D 塞尔达巅峰之作！皮克罗童话世界与神奇之帽。",
        "badge": "ARPG 巅峰 · 满分神作",
        "featured": True
    },
    "castlevania_aria_of_sorrow": {
        "title": "恶魔城：晓月圆舞曲 (Aria of Sorrow)",
        "desc": "GBA 恶魔城三部曲巅峰！灵魂吸收系统、苍真与德古拉之力的终极觉醒。",
        "badge": "类银河恶魔城 · 传世经典",
        "featured": True
    },
    "kirby_amazing_mirror": {
        "title": "星之卡比：镜之大迷宫 (Amazing Mirror)",
        "desc": "四只不同颜色的卡比勇闯镜之国！自由探索大地图、电话呼叫伙伴与吞噬变身。",
        "badge": "动作冒险 · 萌系硬核",
        "featured": True
    },
    "super_mario_bros": {
        "title": "超级马里奥兄弟 (Super Mario Bros)",
        "desc": "全球平台跳跃游戏的开山始祖！营救桃花公主，寻找隐藏水管与无尽金币。",
        "badge": "FC 红白机 · 传世殿堂",
        "featured": True
    },
    "contra": {
        "title": "魂斗罗 经典初代 (Contra)",
        "desc": "上上下下左右左右BA！经典的硬派双人射击，激燃的 8-bit 背景音乐与畅快扫射。",
        "badge": "硬核射击 · 童年回忆",
        "featured": True
    },
    "battle_city": {
        "title": "坦克大战 (Battle City)",
        "desc": "保护雄鹰基地！双人协作打砖块、吃钢盔升级火力与吃手雷清全屏。",
        "badge": "童年神作 · 双人对战",
        "featured": True
    },
    "castlevania": {
        "title": "恶魔城 初代 (Castlevania)",
        "desc": "西蒙·贝尔蒙特手握圣鞭初探恶魔之城，开创恶魔城传奇史诗的元祖篇章。",
        "badge": "FC 硬核 · 哥特动作",
        "featured": False
    },
    "airball": {
        "title": "空中滚球 (Airball 3D)",
        "desc": "GBA 平台经典的等轴 3D 物理平衡解谜游戏，挑战空间逻辑与微操极限。",
        "badge": "3D 物理解谜",
        "featured": False
    },
    "ambushed": {
        "title": "伏击 (Ambushed)",
        "desc": "开源复古俯视角动作战术射击，体验极速走位与子弹风暴。",
        "badge": "独立战术射击",
        "featured": False
    }
}


class RetroHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        # Silence high-frequency endpoints to save OpenStick CPU and disk I/O
        if args and len(args) > 0 and isinstance(args[0], str):
            first = args[0]
            if any(noisy in first for noisy in ("/api/cloud/input", "/api/cloud/heartbeat", "/api/cloud/status", "/api/cloud/stream")):
                return
        super().log_message(format, *args)

    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Range, Authorization")

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_cors_headers()
        self.end_headers()

    def do_HEAD(self):
        # Handle HEAD request same as GET but without body
        self.do_GET()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/" or path == "/index.html":
            return self.serve_file(os.path.join(PUBLIC_DIR, "index.html"), "text/html")
        elif path == "/emulator.html":
            return self.serve_file(os.path.join(PUBLIC_DIR, "emulator.html"), "text/html")
        elif path == "/cloud_player.html":
            return self.serve_file(os.path.join(PUBLIC_DIR, "cloud_player.html"), "text/html")
        elif path == "/api/games":
            return self.handle_list_games()
        elif path == "/api/status":
            return self.handle_status()
        elif path == "/api/cloud/stream":
            return self.handle_cloud_stream()
        elif path == "/api/cloud/status":
            if slot_mgr:
                query = parse_qs(parsed.query)
                session_id = query.get("session_id", [None])[0]
                return self.send_json_response(slot_mgr.get_status(session_id))
            else:
                return self.send_json_response({"running": False, "error": "Slot manager unavailable"})
        elif path.startswith("/api/roms/"):
            rel_rom = path[len("/api/roms/"):]
            full_path = os.path.normpath(os.path.join(ROMS_DIR, rel_rom))
            if full_path.startswith(ROMS_DIR) and os.path.isfile(full_path):
                return self.serve_file_ranged(full_path, "application/octet-stream")
            else:
                self.send_error(HTTPStatus.NOT_FOUND, "ROM Not Found")
        elif path.startswith("/api/covers/"):
            filename = os.path.basename(path)
            full_path = os.path.join(COVERS_DIR, filename)
            if os.path.isfile(full_path):
                mime, _ = mimetypes.guess_type(full_path)
                return self.serve_file(full_path, mime or "image/png")
            else:
                # Return standard fallback SVG cover
                return self.serve_fallback_svg(filename)
        elif path.startswith("/api/saves/"):
            game_id = os.path.basename(path)
            save_path = os.path.join(SAVES_DIR, f"{game_id}.state")
            if os.path.isfile(save_path):
                return self.serve_file(save_path, "application/octet-stream")
            else:
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_cors_headers()
                self.end_headers()
        else:
            # Try serving from public folder
            rel_path = path.lstrip("/")
            full_path = os.path.normpath(os.path.join(PUBLIC_DIR, rel_path))
            if full_path.startswith(PUBLIC_DIR) and os.path.isfile(full_path):
                mime, _ = mimetypes.guess_type(full_path)
                return self.serve_file(full_path, mime or "application/octet-stream")
            else:
                self.send_error(HTTPStatus.NOT_FOUND, "Page Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/api/upload":
            return self.handle_upload()
        elif path.startswith("/api/saves/"):
            game_id = os.path.basename(path)
            return self.handle_save_upload(game_id)
        elif path == "/api/cloud/join":
            return self.handle_cloud_join()
        elif path == "/api/cloud/heartbeat":
            return self.handle_cloud_heartbeat()
        elif path == "/api/cloud/leave":
            return self.handle_cloud_leave()
        elif path == "/api/cloud/start":
            return self.handle_cloud_start()
        elif path == "/api/cloud/stop":
            if cloud_engine:
                cloud_engine.stop_game()
            return self.send_json_response({"status": "ok", "message": "Cloud session stopped"})
        elif path == "/api/cloud/reset":
            if slot_mgr:
                slot_mgr.reset_all()
            return self.send_json_response({"status": "ok", "message": "All slots reset"})
        elif path == "/api/cloud/takeover":
            return self.handle_cloud_takeover()
        elif path == "/api/cloud/input":
            return self.handle_cloud_input()
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Endpoint not found")

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path.startswith("/api/games/"):
            rel_rom = path[len("/api/games/"):]
            full_path = os.path.normpath(os.path.join(ROMS_DIR, rel_rom))
            if full_path.startswith(ROMS_DIR) and os.path.isfile(full_path):
                try:
                    os.remove(full_path)
                    # Check cover
                    base_name, _ = os.path.splitext(os.path.basename(full_path))
                    cover_cand = os.path.join(COVERS_DIR, f"{base_name}.png")
                    if os.path.exists(cover_cand):
                        os.remove(cover_cand)
                    self.send_json_response({"status": "success", "message": f"Deleted {rel_rom}"})
                except Exception as e:
                    self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))
            else:
                self.send_error(HTTPStatus.NOT_FOUND, "ROM not found")
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def handle_list_games(self):
        games = []
        for root, dirs, files in os.walk(ROMS_DIR):
            for file in sorted(files):
                ext = os.path.splitext(file)[1].lower()
                if ext in SYSTEM_MAPPING:
                    sys_info = SYSTEM_MAPPING[ext]
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, ROMS_DIR).replace("\\", "/")
                    base_name = os.path.splitext(file)[0].lower()
                    
                    override = GAME_INFO_OVERRIDE.get(base_name, {})
                    title = override.get("title", base_name.replace("_", " ").replace("-", " ").title())
                    desc = override.get("desc", f"运行在 {sys_info['name']} 模拟内核上的经典游戏。")
                    badge = override.get("badge", sys_info["system"].upper())
                    featured = override.get("featured", False)

                    # Cover lookup
                    cover_url = f"/api/covers/{base_name}.png"
                    if not os.path.exists(os.path.join(COVERS_DIR, f"{base_name}.png")):
                        cover_url = f"/api/covers/{base_name}.svg"

                    games.append({
                        "id": base_name.replace(" ", "-"),
                        "title": title,
                        "desc": desc,
                        "badge": badge,
                        "featured": featured,
                        "filename": file,
                        "rel_path": rel_path,
                        "system": sys_info["system"],
                        "system_name": sys_info["name"],
                        "core": sys_info["core"],
                        "size_mb": round(os.path.getsize(full_path) / (1024 * 1024), 2),
                        "cover": cover_url,
                        "rom_url": f"/api/roms/{rel_path}"
                    })

        self.send_json_response(games)

    def handle_status(self):
        self.send_json_response(get_system_stats())

    def handle_cloud_stream(self):
        if not slot_mgr:
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Slot manager unavailable")
            return
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        session_id = qs.get("session_id", [None])[0]
        slot = int(qs.get("slot", [0])[0])
        
        target_port = slot_mgr.get_stream_port(session_id=session_id, slot=slot)
        
        # Connect to worker via raw TCP socket with retries (up to 2.5s)
        s = None
        for _ in range(25):
            try:
                s = socket.create_connection(("127.0.0.1", target_port), timeout=1.0)
                break
            except Exception:
                time.sleep(0.1)
                
        if not s:
            self.send_error(HTTPStatus.NOT_FOUND, "Worker stream not ready")
            return

        try:
            # Request MJPEG stream from worker process
            s.sendall(b"GET /stream HTTP/1.0\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
            
            # Read until worker's HTTP response headers are consumed
            header_buf = b""
            while b"\r\n\r\n" not in header_buf:
                chunk = s.recv(1024)
                if not chunk:
                    break
                header_buf += chunk
                
            idx = header_buf.find(b"\r\n\r\n")
            body_start = header_buf[idx+4:] if idx != -1 else b""
            
            # Send top-level headers to browser/proxy
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache, private, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_cors_headers()
            self.end_headers()
            
            # Flush initial frame bytes if available
            if body_start:
                self.wfile.write(body_start)
                self.wfile.flush()
                
            # Stream MJPEG frames continuously with zero-copy socket buffer
            while True:
                chunk = s.recv(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            print(f"[STREAM ERROR] target_port={target_port}: {type(e)} {e}", file=sys.stderr, flush=True)
        finally:
            try:
                s.close()
            except Exception:
                pass

    def handle_cloud_join(self):
        if not slot_mgr:
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Slot manager unavailable")
            return
        content_len = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(content_len).decode("utf-8")) if content_len > 0 else {}
        session_id = payload.get("session_id", "anon")
        player_name = payload.get("player_name", "")
        rel_path = payload.get("rel_path", "")
        system = payload.get("system", "nes")
        title = payload.get("title", "")
        info = slot_mgr.join(session_id, player_name, rel_path=rel_path, system=system, title=title, roms_dir=ROMS_DIR)
        self.send_json_response(info)

    def handle_cloud_heartbeat(self):
        if not slot_mgr:
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Slot manager unavailable")
            return
        content_len = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(content_len).decode("utf-8")) if content_len > 0 else {}
        session_id = payload.get("session_id", "")
        info = slot_mgr.heartbeat(session_id)
        self.send_json_response(info)

    def handle_cloud_leave(self):
        if not slot_mgr:
            self.send_json_response({"status": "ok"})
            return
        content_len = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(content_len).decode("utf-8")) if content_len > 0 else {}
        session_id = payload.get("session_id", "")
        slot_mgr.leave(session_id)
        self.send_json_response({"status": "ok"})

    def handle_cloud_start(self):
        if not slot_mgr:
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Slot manager unavailable")
            return
        content_len = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(content_len).decode("utf-8"))
        rel_path = payload.get("rel_path", "")
        system = payload.get("system", "nes")
        title = payload.get("title", "")
        session_id = payload.get("session_id", "")
        player_name = payload.get("player_name", "")
        force_restart = bool(payload.get("force_restart", False))
        info = slot_mgr.join(session_id, player_name, rel_path=rel_path, system=system, title=title, roms_dir=ROMS_DIR, force_restart=force_restart)
        self.send_json_response({"status": "ok", "message": f"Ready: {title}", "queue": info})

    def handle_cloud_input(self):
        if not slot_mgr:
            self.send_json_response({"status": "ignored"})
            return
        content_len = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(content_len).decode("utf-8")) if content_len > 0 else {}
        btn = payload.get("btn", "")
        state = int(payload.get("state", 0))
        session_id = payload.get("session_id", None)
        active = payload.get("active", None)
        seq = int(payload.get("seq", 0))
        ok = slot_mgr.forward_input(session_id, btn, state, active_buttons=active, seq=seq)
        self.send_json_response({"status": "ok" if ok else "blocked"})

    def handle_cloud_takeover(self):
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8")
        payload = json.loads(body) if body else {}
        session_id = payload.get("session_id", None)
        player_name = payload.get("player_name", "")
        if not slot_mgr:
            self.send_json_response({"status": "error", "message": "Slot manager unavailable"}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        res = slot_mgr.takeover_slot(session_id, player_name)
        self.send_json_response(res)

    def handle_save_upload(self, game_id):
        content_len = int(self.headers.get("Content-Length", 0))
        if content_len == 0:
            self.send_error(HTTPStatus.BAD_REQUEST, "Empty payload")
            return

        save_data = self.rfile.read(content_len)
        save_path = os.path.join(SAVES_DIR, f"{game_id}.state")
        try:
            with open(save_path, "wb") as f:
                f.write(save_data)
            self.send_json_response({"status": "ok", "saved_bytes": len(save_data)})
        except Exception as e:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, f"Failed to save state: {e}")

    def handle_upload(self):
        """Handle raw or multipart file uploads."""
        content_type = self.headers.get("Content-Type", "")
        content_len = int(self.headers.get("Content-Length", 0))

        if "multipart/form-data" in content_type:
            boundary = content_type.split("boundary=")[1].encode()
            data = self.rfile.read(content_len)
            parts = data.split(b"--" + boundary)

            saved_files = []
            for part in parts:
                if b'filename="' in part:
                    header_part, body = part.split(b"\r\n\r\n", 1)
                    body = body.rstrip(b"\r\n--")
                    m = re.search(r'filename="([^"]+)"', header_part.decode(errors="ignore"))
                    if m:
                        filename = os.path.basename(m.group(1))
                        ext = os.path.splitext(filename)[1].lower()
                        if ext in SYSTEM_MAPPING:
                            system = SYSTEM_MAPPING[ext]["system"]
                            dest = os.path.join(ROMS_DIR, system, filename)
                            with open(dest, "wb") as f:
                                f.write(body)
                            saved_files.append(filename)
                        elif ext in [".png", ".jpg", ".jpeg", ".webp"]:
                            dest = os.path.join(COVERS_DIR, filename)
                            with open(dest, "wb") as f:
                                f.write(body)
                            saved_files.append(filename)

            self.send_json_response({"status": "success", "uploaded": saved_files})
        else:
            self.send_error(HTTPStatus.BAD_REQUEST, "Requires multipart/form-data")

    def serve_fallback_svg(self, filename):
        name = os.path.splitext(filename)[0].replace("_", " ").title()
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="300" height="400" viewBox="0 0 300 400">
          <defs>
            <linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stop-color="#141E30"/>
              <stop offset="100%" stop-color="#243B55"/>
            </linearGradient>
          </defs>
          <rect width="300" height="400" fill="url(#g)" rx="16"/>
          <circle cx="150" cy="160" r="55" fill="none" stroke="#00f2fe" stroke-width="4" opacity="0.6"/>
          <path d="M135 135 L175 160 L135 185 Z" fill="#00f2fe"/>
          <text x="150" y="270" font-family="-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif" font-size="18" font-weight="bold" fill="#ffffff" text-anchor="middle">{name[:18]}</text>
          <text x="150" y="300" font-family="sans-serif" font-size="12" fill="#4facfe" text-anchor="middle">RetroStick Game</text>
        </svg>"""
        self.serve_bytes(svg.encode("utf-8"), "image/svg+xml")

    def serve_file(self, filepath, mime_type):
        try:
            with open(filepath, "rb") as f:
                content = f.read()
            self.serve_bytes(content, mime_type)
        except Exception as e:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

    def serve_bytes(self, content, mime_type):
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(content)))
        if "html" in mime_type or "json" in mime_type:
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(content)

    def serve_file_ranged(self, filepath, mime_type):
        """Serve file with HTTP Range support for big ROM files."""
        try:
            file_size = os.path.getsize(filepath)
            range_header = self.headers.get("Range", None)

            if not range_header:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", mime_type)
                self.send_header("Content-Length", str(file_size))
                self.send_header("Accept-Ranges", "bytes")
                self.send_cors_headers()
                self.end_headers()
                with open(filepath, "rb") as f:
                    shutil.copyfileobj(f, self.wfile)
                return

            # Parse range: "bytes=0-1024"
            match = re.match(r"bytes=(\d+)-(\d*)", range_header)
            if not match:
                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                return

            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else file_size - 1

            if start >= file_size or end >= file_size or start > end:
                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                return

            length = end - start + 1
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_cors_headers()
            self.end_headers()

            with open(filepath, "rb") as f:
                f.seek(start)
                bytes_left = length
                while bytes_left > 0:
                    chunk = f.read(min(bytes_left, 65536))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    bytes_left -= len(chunk)
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

    def send_json_response(self, data):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(body)


def run_server(port=8088):
    server_address = ("0.0.0.0", port)
    httpd = ThreadingHTTPServer(server_address, RetroHandler)
    print("=" * 65)
    print(f" 🎮 RetroStick Cloud Console is running on port {port} 🎮")
    print(f" Local Web Access: http://192.168.2.203:{port}")
    print("=" * 65)
    httpd.serve_forever()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8088
    run_server(port)
