#!/usr/bin/env python3
"""
Multi-Slot Concurrent Cloud Gaming & Queue Manager for RetroStick.
Supports multiple concurrent hardware-accelerated slots (default: 2 slots on Adreno 306),
with automatic idle timeouts, fair turn limits, FIFO queueing, and spectator mode.
"""

import os
import sys
import time
import json
import threading
import subprocess
import urllib.request
import urllib.error
import socket

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKER_SCRIPT = os.path.join(BASE_DIR, "game_worker.py")
PYTHON_BIN = sys.executable

class SlotManager:
    def __init__(self, max_slots=2, idle_timeout_sec=60, max_turn_sec=1200, dc_timeout_sec=60):
        self.max_slots = max_slots
        self.idle_timeout_sec = idle_timeout_sec
        self.max_turn_sec = max_turn_sec
        self.dc_timeout_sec = dc_timeout_sec
        
        # Fast UDP socket for zero-thread microsecond input forwarding
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        # slots: {0: None, 1: None}
        self.slots = {i: None for i in range(max_slots)}
        self.waiting_queue = [] # [{session_id, player_name, joined_at, last_heartbeat, game_req}]
        self.lock = threading.Lock()

    def _get_worker_port(self, slot_id):
        return 8091 + slot_id

    def _spawn_worker(self, slot_id, rom_path, system, title):
        port = self._get_worker_port(slot_id)
        # Kill any zombie on that port first
        try:
            subprocess.run(["pkill", "-9", "-f", f"game_worker.py --port {port}"], timeout=2)
            time.sleep(0.15)
        except Exception:
            pass

        cmd = [
            PYTHON_BIN, WORKER_SCRIPT,
            "--port", str(port),
            "--rom", rom_path,
            "--system", system,
            "--title", title
        ]
        proc = subprocess.Popen(cmd)
        
        # Wait up to 2 seconds for worker HTTP to come alive
        alive = False
        for _ in range(25):
            time.sleep(0.1)
            try:
                res = urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=0.2)
                if res.status == 200:
                    alive = True
                    break
            except Exception:
                pass

        if not alive:
            print(f"[SlotManager] Warning: worker on port {port} slow to respond", file=sys.stderr)
        return proc, port

    def _stop_slot(self, slot_id):
        slot = self.slots.get(slot_id)
        if slot:
            port = slot["port"]
            proc = slot.get("process")
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{port}/stop", data=b"{}", headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=0.5)
            except Exception:
                pass
            if proc:
                try:
                    proc.terminate()
                    proc.wait(timeout=1.0)
                except Exception:
                    try: proc.kill()
                    except Exception: pass
            self.slots[slot_id] = None
            print(f"[SlotManager] Slot {slot_id} freed.")

    def join(self, session_id, player_name="", rel_path="", system="nes", title="", roms_dir=""):
        with self.lock:
            self._tick()
            now = time.time()
            if not player_name:
                player_name = f"玩家 #{session_id[-4:].upper()}"

            full_path = os.path.normpath(os.path.join(roms_dir, rel_path)) if rel_path else ""

            # 1. Player already occupies an active slot
            for slot_id, slot in self.slots.items():
                if slot and slot["session_id"] == session_id:
                    slot["last_heartbeat"] = now
                    # If game changed, reload worker
                    if full_path and slot["rom_path"] != full_path:
                        self._stop_slot(slot_id)
                        proc, port = self._spawn_worker(slot_id, full_path, system, title)
                        self.slots[slot_id] = {
                            "session_id": session_id,
                            "player_name": player_name,
                            "port": port,
                            "process": proc,
                            "rom_path": full_path,
                            "system": system,
                            "title": title,
                            "start_time": now,
                            "last_input": now,
                            "last_heartbeat": now
                        }
                    elapsed = now - self.slots[slot_id]["start_time"]
                    time_left = max(1, int(self.max_turn_sec - elapsed))
                    return self._make_player_resp(session_id, slot_id, time_left)

            # 2. Check for free slot
            free_slot_id = None
            for slot_id in range(self.max_slots):
                if self.slots[slot_id] is None:
                    free_slot_id = slot_id
                    break

            if free_slot_id is not None and full_path and os.path.isfile(full_path):
                proc, port = self._spawn_worker(free_slot_id, full_path, system, title)
                self.slots[free_slot_id] = {
                    "session_id": session_id,
                    "player_name": player_name,
                    "port": port,
                    "process": proc,
                    "rom_path": full_path,
                    "system": system,
                    "title": title,
                    "start_time": now,
                    "last_input": now,
                    "last_heartbeat": now
                }
                print(f"[SlotManager] Granted Slot {free_slot_id} to {player_name} ({title})")
                return self._make_player_resp(session_id, free_slot_id, self.max_turn_sec)

            # 3. All slots full -> Put in Waiting Queue
            game_req = {"rel_path": rel_path, "system": system, "title": title}
            # Update existing queue entry or append
            for i, item in enumerate(self.waiting_queue):
                if item["session_id"] == session_id:
                    item["last_heartbeat"] = now
                    item["player_name"] = player_name
                    item["game_req"] = game_req
                    return self._make_queued_resp(session_id, i + 1)

            self.waiting_queue.append({
                "session_id": session_id,
                "player_name": player_name,
                "joined_at": now,
                "last_heartbeat": now,
                "game_req": game_req
            })
            pos = len(self.waiting_queue)
            print(f"[SlotManager] {player_name} queued at position #{pos}")
            return self._make_queued_resp(session_id, pos)

    def heartbeat(self, session_id):
        with self.lock:
            self._tick()
            now = time.time()

            # Active player
            for slot_id, slot in self.slots.items():
                if slot and slot["session_id"] == session_id:
                    slot["last_heartbeat"] = now
                    elapsed = now - slot["start_time"]
                    time_left = max(1, int(self.max_turn_sec - elapsed))
                    return self._make_player_resp(session_id, slot_id, time_left)

            # Queued player
            for i, item in enumerate(self.waiting_queue):
                if item["session_id"] == session_id:
                    item["last_heartbeat"] = now
                    return self._make_queued_resp(session_id, i + 1)

            # Spectator
            return self._make_spectator_resp(session_id)

    def leave(self, session_id):
        with self.lock:
            for slot_id, slot in self.slots.items():
                if slot and slot["session_id"] == session_id:
                    print(f"[SlotManager] Player {slot['player_name']} left slot {slot_id}.")
                    self._stop_slot(slot_id)
                    self._promote_next(slot_id)
                    return True
            self.waiting_queue = [q for q in self.waiting_queue if q["session_id"] != session_id]
            return True

    def forward_input(self, session_id, btn, state, active_buttons=None):
        with self.lock:
            for slot_id, slot in self.slots.items():
                if slot and slot["session_id"] == session_id:
                    slot["last_input"] = time.time()
                    port = slot["port"]
                    try:
                        if active_buttons is not None:
                            payload = f"SYNC:{','.join(active_buttons)}".encode("ascii")
                        else:
                            payload = f"{btn}:{state}".encode("ascii")
                        self.udp_sock.sendto(payload, ("127.0.0.1", port))
                    except Exception:
                        threading.Thread(target=self._send_input_http, args=(port, btn, state), daemon=True).start()
                    return True
            return False

    def _send_input_http(self, port, btn, state):
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/input",
                data=json.dumps({"btn": btn, "state": state}).encode(),
                headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=0.2)
        except Exception:
            pass

    def get_stream_port(self, session_id=None, slot=0):
        with self.lock:
            # If session is active, stream its own slot
            if session_id:
                for slot_id, s in self.slots.items():
                    if s and s["session_id"] == session_id:
                        return s["port"]
            # Otherwise return requested slot or first active slot
            if slot in self.slots and self.slots[slot]:
                return self.slots[slot]["port"]
            for s in self.slots.values():
                if s:
                    return s["port"]
            return 8091 # Default fallback

    def _promote_next(self, vacant_slot_id):
        now = time.time()
        alive = [q for q in self.waiting_queue if (now - q["last_heartbeat"]) < self.dc_timeout_sec]
        self.waiting_queue = alive

        if alive:
            next_user = self.waiting_queue.pop(0)
            req = next_user.get("game_req", {})
            roms_dir = os.path.join(BASE_DIR, "roms")
            rel_path = req.get("rel_path", "nes/super_mario_bros.nes")
            full_path = os.path.normpath(os.path.join(roms_dir, rel_path))
            system = req.get("system", "nes")
            title = req.get("title", "经典游戏")

            proc, port = self._spawn_worker(vacant_slot_id, full_path, system, title)
            self.slots[vacant_slot_id] = {
                "session_id": next_user["session_id"],
                "player_name": next_user["player_name"],
                "port": port,
                "process": proc,
                "rom_path": full_path,
                "system": system,
                "title": title,
                "start_time": now,
                "last_input": now,
                "last_heartbeat": now
            }
            print(f"[SlotManager] Promoted {next_user['player_name']} from queue to Slot {vacant_slot_id} ({title})!")
            return True
        return False

    def _tick(self):
        now = time.time()
        # Clean disconnected queue entries
        self.waiting_queue = [q for q in self.waiting_queue if (now - q["last_heartbeat"]) < self.dc_timeout_sec]

        for slot_id, slot in list(self.slots.items()):
            if not slot:
                continue

            # 1. Disconnect check
            if (now - slot["last_heartbeat"]) > self.dc_timeout_sec:
                print(f"[SlotManager] Slot {slot_id} ({slot['player_name']}) heartbeat timeout (disconnected).")
                self._stop_slot(slot_id)
                self._promote_next(slot_id)
                continue

            # 2. Idle timeout & turn limit (only enforced if someone is waiting in queue!)
            if self.waiting_queue:
                # If queue pressure just began, give player a full turn from this moment
                if not slot.get("queue_pressure_started"):
                    slot["queue_pressure_started"] = True
                    slot["start_time"] = now

                elapsed = now - slot["start_time"]
                idle = now - slot["last_input"]

                if idle >= self.idle_timeout_sec:
                    print(f"[SlotManager] Slot {slot_id} ({slot['player_name']}) kicked for {self.idle_timeout_sec}s idle while queue waiting.")
                    self._stop_slot(slot_id)
                    self._promote_next(slot_id)
                    continue

                if elapsed >= self.max_turn_sec:
                    print(f"[SlotManager] Slot {slot_id} ({slot['player_name']}) turn expired ({self.max_turn_sec}s).")
                    self._stop_slot(slot_id)
                    self._promote_next(slot_id)
                    continue
            else:
                # No queue -> unlimited play! Keep refreshing start_time so elapsed stays 0
                slot["queue_pressure_started"] = False
                slot["start_time"] = now

    def _get_active_players_summary(self):
        active = []
        is_unlimited = (len(self.waiting_queue) == 0)
        now = time.time()
        for slot_id, slot in self.slots.items():
            if slot:
                elapsed = now - slot["start_time"]
                time_left = self.max_turn_sec if is_unlimited else max(1, int(self.max_turn_sec - elapsed))
                active.append({
                    "slot_id": slot_id,
                    "player_name": slot["player_name"],
                    "title": slot["title"],
                    "system": slot["system"],
                    "time_left_sec": time_left,
                    "is_unlimited": is_unlimited
                })
        return active

    def _make_player_resp(self, session_id, slot_id, time_left):
        is_unlimited = (len(self.waiting_queue) == 0)
        return {
            "role": "player",
            "session_id": session_id,
            "slot_id": slot_id,
            "time_left_sec": self.max_turn_sec if is_unlimited else time_left,
            "is_unlimited": is_unlimited,
            "max_slots": self.max_slots,
            "queue_len": len(self.waiting_queue),
            "active_players": self._get_active_players_summary()
        }

    def _make_queued_resp(self, session_id, position):
        active = self._get_active_players_summary()
        # Find shortest time left among active slots
        shortest_left = min([p["time_left_sec"] for p in active]) if active else self.max_turn_sec
        est_wait = shortest_left + ((position - 1) * (self.max_turn_sec // max(1, self.max_slots)))
        return {
            "role": "queued",
            "session_id": session_id,
            "queue_position": position,
            "queue_len": len(self.waiting_queue),
            "est_wait_sec": max(5, est_wait),
            "max_slots": self.max_slots,
            "active_players": active
        }

    def _make_spectator_resp(self, session_id):
        return {
            "role": "spectator",
            "session_id": session_id,
            "max_slots": self.max_slots,
            "queue_len": len(self.waiting_queue),
            "active_players": self._get_active_players_summary()
        }

    def get_status(self, session_id=None):
        with self.lock:
            self._tick()
            active_summary = self._get_active_players_summary()
            
            # live telemetry from slot 0 or 1
            fps = 0.0
            temp = "45°C"
            for slot in self.slots.values():
                if slot:
                    port = slot["port"]
                    try:
                        res = urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=0.2)
                        if res.status == 200:
                            data = json.loads(res.read().decode())
                            fps = max(fps, data.get("fps", 0.0))
                            temp = data.get("cpu_temp", temp)
                    except Exception:
                        pass

            q_info = self._make_spectator_resp(session_id)
            if session_id:
                for slot_id, s in self.slots.items():
                    if s and s["session_id"] == session_id:
                        elapsed = time.time() - s["start_time"]
                        q_info = self._make_player_resp(session_id, slot_id, max(1, int(self.max_turn_sec - elapsed)))
                        break
                for i, item in enumerate(self.waiting_queue):
                    if item["session_id"] == session_id:
                        q_info = self._make_queued_resp(session_id, i + 1)
                        break

            return {
                "running": len(active_summary) > 0,
                "fps": fps,
                "cpu_temp": temp,
                "gpu_device": "Qualcomm Adreno 306 (/dev/dri/renderD128)",
                "max_slots": self.max_slots,
                "active_count": len(active_summary),
                "active_players": active_summary,
                "queue": q_info
            }
