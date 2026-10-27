#!/usr/bin/env bash
# Cloudflare Tunnel Launcher for RetroStick
LOG_FILE="/var/log/retro-tunnel.log"
URL_FILE="/opt/retro-console/public/tunnel_url.txt"

rm -f "$LOG_FILE"
/usr/local/bin/cloudflared tunnel --url http://127.0.0.1:8088 > "$LOG_FILE" 2>&1 &
PID=$!

(
  for i in {1..30}; do
    URL=$(grep -oE "https://[a-zA-Z0-9-]+\.trycloudflare\.com" "$LOG_FILE" 2>/dev/null | head -n 1)
    if [ -n "$URL" ]; then
      echo "$URL" > "$URL_FILE"
      echo "=== Public Tunnel Active: $URL ==="
      break
    fi
    sleep 1
  done
) &

wait $PID
