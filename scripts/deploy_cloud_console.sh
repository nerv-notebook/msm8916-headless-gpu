#!/usr/bin/env bash
# Deploy RetroStick Adreno 306 Cloud Console to Qualcomm MSM8916 Stick
# Usage: ./scripts/deploy_cloud_console.sh [ssh_host_or_ip]

set -e

HOST="${1:-ufi001b}"
REMOTE_DIR="/opt/retro-console"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== [1/4] Preparing Remote Directories on $HOST ==="
ssh "$HOST" "mkdir -p $REMOTE_DIR/{public,roms/{gba,nes,snes,gbc,segaMD},covers,saves}"

echo "=== [2/4] Syncing Cloud Console Engine & Web Assets ==="
scp "$REPO_DIR/server.py" "$REPO_DIR/slot_manager.py" "$REPO_DIR/game_worker.py" "$HOST:$REMOTE_DIR/"
scp -r "$REPO_DIR/public/"* "$HOST:$REMOTE_DIR/public/"
if [ -d "$REPO_DIR/covers" ]; then
  scp -r "$REPO_DIR/covers/"* "$HOST:$REMOTE_DIR/covers/"
fi
scp "$REPO_DIR/scripts/start_tunnel.sh" "$HOST:$REMOTE_DIR/"
ssh "$HOST" "chmod +x $REMOTE_DIR/server.py $REMOTE_DIR/start_tunnel.sh"

echo "=== [3/4] Configuring Systemd Auto-Start Services ==="
ssh "$HOST" "cat << 'EOF' > /etc/systemd/system/retro-console.service
[Unit]
Description=RetroStick Cloud Retro Console Server (Adreno 306 GLES)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/retro-console
ExecStart=/usr/bin/python3 -u /opt/retro-console/server.py 8088
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat << 'EOF' > /etc/systemd/system/retro-tunnel.service
[Unit]
Description=Cloudflare Public Tunnel for RetroStick
After=network.target retro-console.service
Wants=retro-console.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/retro-console
ExecStart=/opt/retro-console/start_tunnel.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now retro-console.service
systemctl enable --now retro-tunnel.service
systemctl restart retro-console.service
"

echo "=== [4/4] Verifying Service Status ==="
ssh "$HOST" "systemctl status retro-console.service --no-pager"

echo ""
echo "======================================================================="
echo " 🎉 RetroStick Adreno 306 Cloud Console is successfully deployed!"
echo " Local Access:  http://$HOST:8088"
echo "======================================================================="
