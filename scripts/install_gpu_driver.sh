#!/usr/bin/env bash
# ==============================================================================
# One-Click Installer for Qualcomm MSM8916 Adreno 306 Headless GPU Driver
# Target: OpenStick / UFI001B / UFI001C / UZ801 running Linux 5.15.x arm64
# ==============================================================================

set -e

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}======================================================================${NC}"
echo -e "${GREEN}   Qualcomm MSM8916 Adreno 306 Headless GPU Driver Installer${NC}"
echo -e "${BLUE}======================================================================${NC}"

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}[ERROR] This script must be run as root. Try 'sudo bash $0'.${NC}"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
BIN_DIR="$REPO_DIR/bin"

KERNEL_VER="$(uname -r)"
echo -e "${BLUE}[INFO] Detected Kernel Version:${NC} $KERNEL_VER"
echo -e "${BLUE}[INFO] Architecture:${NC} $(uname -m)"

if [ "$(uname -m)" != "aarch64" ]; then
    echo -e "${RED}[ERROR] Architecture must be aarch64 (ARM64). Aborting.${NC}"
    exit 1
fi

MODULE_DEST_DIR="/lib/modules/$KERNEL_VER/kernel/drivers/gpu/drm"
mkdir -p "$MODULE_DEST_DIR"

if [ ! -f "$BIN_DIR/msm.ko" ] || [ ! -f "$BIN_DIR/gpu-sched.ko" ]; then
    echo -e "${RED}[ERROR] Binary kernel modules not found in $BIN_DIR!${NC}"
    exit 1
fi

echo -e "${BLUE}[1/5] Installing kernel modules to ${MODULE_DEST_DIR}...${NC}"
cp -v "$BIN_DIR/gpu-sched.ko" "$MODULE_DEST_DIR/"
cp -v "$BIN_DIR/msm.ko" "$MODULE_DEST_DIR/"
chmod 644 "$MODULE_DEST_DIR/gpu-sched.ko" "$MODULE_DEST_DIR/msm.ko"

echo -e "${BLUE}[2/5] Updating module dependencies (depmod)...${NC}"
depmod -a

echo -e "${BLUE}[3/5] Configuring auto-load on boot (/etc/modules-load.d/gpu.conf)...${NC}"
cat << 'EOF' > /etc/modules-load.d/gpu.conf
# Automatically load MSM DRM and GPU scheduler for Adreno 306
gpu-sched
msm
EOF

echo -e "${BLUE}[4/5] Loading GPU kernel modules...${NC}"
modprobe gpu-sched 2>/dev/null || insmod "$MODULE_DEST_DIR/gpu-sched.ko" || true
modprobe msm 2>/dev/null || insmod "$MODULE_DEST_DIR/msm.ko" || true

echo -e "${BLUE}[5/5] Verifying DRM render node...${NC}"
sleep 1

if [ -e "/dev/dri/renderD128" ]; then
    echo -e "${GREEN}======================================================================${NC}"
    echo -e "${GREEN}[SUCCESS] Adreno 306 GPU is successfully activated!${NC}"
    echo -e "${GREEN}DRM Device: /dev/dri/renderD128${NC}"
    echo -e "${GREEN}======================================================================${NC}"
    echo ""
    echo "To test 3D hardware acceleration:"
    echo "  sudo apt-get install -y python3 libdrm-freedreno1 libgbm1 libegl1-mesa libgles2"
    echo "  python3 $SCRIPT_DIR/test_adreno_3d.py"
    echo ""
else
    echo -e "${YELLOW}[WARNING] /dev/dri/renderD128 was not created immediately.${NC}"
    echo "Checking dmesg output for details:"
    dmesg | grep -E "msm|adreno|drm" | tail -n 15
    echo -e "${YELLOW}Please check firmware files in /lib/firmware/qcom/ (a300_pm4.fw, a300_pfp.fw).${NC}"
fi
