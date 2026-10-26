# Qualcomm MSM8916 (Snapdragon 410) Adreno 306 Headless GPU DRM Driver

[English](README.md) | [简体中文](README.zh-CN.md)

> **高通骁龙 410 (MSM8916 / 4G 随身 WiFi) 无头 Adreno 306 独立显卡驱动补丁与部署套件**  
> 突破无屏幕限制，让随身 WiFi 彻底激活硬件 3D GPU 加速与 EGL / OpenGL ES 3.0 离屏渲染！
>
> ⭐ **如果这个项目帮到了你或成功激活了随身 WiFi 的显卡，请在右上角点个 Star ⭐️ 支持一下！欢迎提交 PR 和 Issue！**

[![Hardware Verified](https://img.shields.io/badge/Hardware-Adreno%20306%20(FD307)-brightgreen.svg)]()
[![OpenGL ES](https://img.shields.io/badge/OpenGL%20ES-3.0%20Mesa%20Freedreno-blue.svg)]()
[![Kernel](https://img.shields.io/badge/Kernel-Linux%205.15%20arm64-orange.svg)]()
[![License](https://img.shields.io/badge/License-GPL--2.0%20%7C%20MIT-lightgrey.svg)]()
[![GitHub Stars](https://img.shields.io/github/stars/nerv-notebook/msm8916-headless-gpu?style=social)](https://github.com/nerv-notebook/msm8916-headless-gpu)

---

## 📖 1. 背景与核心技术难点 (Background & The Problem)

### 1.1 硬件现状
在市面上常见的 **高通 410 4G 随身 WiFi**（如 UFI001B、UFI001C、UZ801 等 OpenStick 社区设备，搭载 MSM8916 芯片）中，芯片内部实际上原生集成了一颗强劲的 **Qualcomm Adreno 306 (A306) 3D GPU**，支持 OpenGL ES 3.0 与 OpenCL/EGL 硬件加速。

然而，在刷入 Debian / Alpine Linux 后，所有玩家都会发现一个令人沮丧的现象：
```bash
$ ls /dev/dri
ls: cannot access '/dev/dri': No such file or directory
```
哪怕内核树里开启了 `CONFIG_DRM_MSM`，系统启动后也根本找不到任何显卡设备节点（无 `/dev/dri/cardX`，无 `/dev/dri/renderD128`），硬件 GPU 彻底处于沉睡停摆状态。

### 1.2 根本原因定位 (Root Cause Analysis)
经过深度追踪 Linux 内核源码 `drivers/gpu/drm/msm/` 架构，发现了该问题的根本原因：

1. **单体驱动组件绑定机制 (Monolithic DRM Component Model)**：
   高通的主线 MSM DRM 驱动依赖 display controller（显示引擎控制器，即 `qcom,mdss` 或 `qcom,mdp5`）作为 **Master Component**（主设备）。由 MDSS 负责汇聚探测所有子组件（包括 DSI 屏幕、HDMI/DP 接口，以及 Adreno 3D GPU `qcom,adreno`）。
2. **随身 WiFi 的无头设备树 (Headless DTB)**：
   随身 WiFi 硬件上没有外接屏幕或 LCD 排线，因此设备树（Device Tree）中明确将 `mdss@1a00000` 标记为 `status = "disabled"`。
3. **死锁等待现象**：
   在内核启动时，`adreno_probe()` 会调用 `component_add(&pdev->dev, &a3xx_ops)` 将 Adreno GPU 注册为 DRM 子组件，然后静静等待 Master 唤醒它。然而，因为 `mdss` 被禁用了，`msm_pdev_probe()` **永远不会被触发**！
   结果就是：Adreno GPU 被永久悬空挂起，无法完成 bind，`/dev/dri/renderD128` 永远不会被创建。

---

## 💡 2. 解决方案：自主无头注册补丁 (The Solution)

在主线内核 `msm_drv.c` 中，实际上保留了一个原本仅针对旧款 i.MX5 (`amd,imageon`) 平台的无头辅助函数：`adreno_device_register_headless()`。该函数可以脱离物理显示控制器，自主构造一个虚拟的 `"msm"` master device，进而触发 Adreno GPU 的全套绑定流程，并生成标准的 DRM Render 节点 `/dev/dri/renderD128`。

我们为 `drivers/gpu/drm/msm/adreno/adreno_device.c` 编写了优雅的架构补丁：
当 `adreno_probe()` 检测到当前系统属于无头平台（即设备树中 `qcom,mdss` 缺失或处于 disabled 状态）时，**自动触发 `adreno_device_register_headless()`**，完成 GPU 独立上线！

### 核心补丁代码（完整补丁见 `patches/`）：
```c
--- a/drivers/gpu/drm/msm/adreno/adreno_device.c
+++ b/drivers/gpu/drm/msm/adreno/adreno_device.c
@@ -561,8 +561,16 @@ static int adreno_probe(struct platform_device *pdev)
 	if (ret)
 		return ret;
 
-	if (of_device_is_compatible(pdev->dev.of_node, "amd,imageon"))
-		adreno_device_register_headless();
+	{
+		struct device_node *mdss_node = of_find_compatible_node(NULL, NULL, "qcom,mdss");
+		/* On headless sticks (e.g. OpenStick / UFI dongles), the display controller
+		 * (qcom,mdss) is disabled or absent. In this case, autonomously register
+		 * a dummy msm master device so the Adreno GPU can bind and expose /dev/dri/renderD128.
+		 */
+		if (of_device_is_compatible(pdev->dev.of_node, "amd,imageon") ||
+		    !mdss_node || !of_device_is_available(mdss_node))
+			adreno_device_register_headless();
+		of_node_put(mdss_node);
+	}
 
 	return 0;
 }
```

---

## 🏆 3. 硬件实测与验证成果 (Verification Proof)

### 3.1 内核硬件加载日志 (`dmesg`)
内核成功将 Adreno 306 加入 IOMMU Group，加载官方固件并注册 DRM 渲染节点：
```text
[   15.421890] adreno 1c00000.gpu: Adding to iommu group 1
[   15.424320] msm msm: bound 1c00000.gpu (ops a3xx_ops [msm])
[   15.431205] [drm] Initialized msm 1.8.0 for msm on minor 1
[   15.467812] msm 1c00000.gpu: loaded qcom/a300_pm4.fw
[   15.472140] msm 1c00000.gpu: loaded qcom/a300_pfp.fw
```

### 3.2 驱动节点生成
```bash
$ ls -la /dev/dri/
crw-rw---- 1 root video  226,   1 Oct 27 03:52 card1
crw-rw-rw- 1 root render 226, 128 Oct 27 03:52 renderD128
```

### 3.3 Mesa Freedreno OpenGL ES 3.0 硬件信息读取
运行本仓库包含的 `test_adreno_3d.py`：
```text
============================================================
 Qualcomm MSM8916 Adreno 306 GPU Hardware Verification Test 
============================================================
[OK] Opened /dev/dri/renderD128 in RDWR mode (FD = 3)
[OK] GBM Device created successfully: 0xaaab0b22a270
[OK] EGL initialized: version 1.4
     EGL Vendor: Mesa Project
     EGL Version: 1.4
[OK] EGL Context created: 0xaaab0b226e60
[OK] EGL Context made current (Headless Surfaceless)

--------------------------------------------------
 >>> GPU VENDOR   : freedreno
 >>> GPU RENDERER : FD307 (Qualcomm Adreno 306)
 >>> GL VERSION   : OpenGL ES 3.0 Mesa 20.3.5
 >>> GLSL VERSION : OpenGL ES GLSL ES 3.00
--------------------------------------------------

[OK] Adreno GPU VRAM Framebuffer Object (FBO) allocated successfully
[TEST] VRAM Pixel Readback: R=255, G=0, B=0, A=255

============================================================
 [SUCCESS] 3D GPU RENDERING PIPELINE IS 100% OPERATIONAL! 
 Hardware Adreno 306 GPU is fully accelerated and functional! 
============================================================
```

---

## ⚡ 4. 硬件性能基准实测数据 (Hardware Performance Benchmarks)

为了全面评估高通 MSM8916 随身 WiFi 作为极低功耗 Linux 边缘服务器以及 Adreno 306 激活后的实际生产力，我们使用 Linux 标准基准工具（`sysbench`、`Direct I/O dd`）与本仓库自带的 GPU 压测工具进行了全维度实测。

### 4.1 综合性能汇总表
| 评测维度 | 测试项目与条件 | 实测指标 | 硬件分析与对应水平 |
| :--- | :--- | :--- | :--- |
| **CPU 单核算力** | Sysbench Prime (10,000, 1 Thread) | **603.53 events/s** (延迟 1.65ms) | 相当于树莓派 3B (4× Cortex-A53 @ 1.2GHz) |
| **CPU 多核算力** | Sysbench Prime (10,000, 4 Threads) | **2409.59 events/s** (延迟 1.66ms) | **多核扩展率高达 99.8%**（$603.5 \times 4 \approx 2410$），无调度瓶颈 |
| **内存读取带宽** | LPDDR3 顺序读取 (Block 1MB, 2GB) | **3,487.40 MiB/s (~3.5 GB/s)** | 平均延迟 0.28ms，充足支撑日常微服务与缓存 |
| **内存写入带宽** | LPDDR3 顺序写入 (Block 1MB, 2GB) | **3,003.41 MiB/s (~3.0 GB/s)** | 平均延迟 0.33ms |
| **闪存物理直写** | eMMC 顺序写入 (`oflag=direct`, 100MB) | **8.0 ~ 8.8 MB/s** | 典型嵌入式 eMMC 4.5 闪存颗粒物理写入寿命与速度 |
| **闪存物理直读** | eMMC 顺序读取 (`iflag=direct`, 100MB) | **51.2 MB/s** | 物理读取速度快，系统引导与程序冷启动迅速 |
| **闪存系统缓存** | Linux Page Cache 缓冲读写 | **291.0 MB/s** | 结合 Linux 内存页缓存机制，日常交互与热文件读写极快 |
| **GPU 3D 渲染** | **Adreno 306 720p (1280×720) 离屏渲染** | **418.0 FPS** (平均帧耗时 2.39ms) | **硬件像素填充率高达 385.2 Mpixels/s** |
| **发热与温控** | 待机温度 ➔ 4核 100% 满载烤机 1 分钟 | **44°C ➔ 48°C** (仅上升 4°C) | 极佳的温控表现，整机功耗仅 1~1.5W，**零降频** |

---

### 4.2 CPU 与内存基准测试详情
通过 `sysbench` 进行标准测试命令：
```bash
# CPU 单核与 4 核并发测试
sysbench cpu --cpu-max-prime=10000 --threads=1 --time=10 run
sysbench cpu --cpu-max-prime=10000 --threads=4 --time=10 run

# LPDDR3 内存读写带宽
sysbench memory --memory-oper=read --memory-block-size=1M --memory-total-size=2G run
sysbench memory --memory-oper=write --memory-block-size=1M --memory-total-size=2G run
```

### 4.3 GPU 硬件 3D 渲染基准测试 (`benchmark_gpu.py`)
我们在本仓库中提供了专用 GPU 压力测试脚本 `scripts/benchmark_gpu.py`，可以直接对 Adreno 306 硬件显存进行 720p/1080p 离屏高压渲染：
```bash
python3 scripts/benchmark_gpu.py
```
**实测输出结果**：
```text
=================================================================
 Qualcomm MSM8916 Adreno 306 GPU Hardware Rendering Benchmark 
=================================================================
Device: FD307 (Qualcomm Adreno 306)

Running 720p (1280x720) benchmark across 500 frames...

--------------------------------------------------
 >>> Total Render Time : 1.196 s
 >>> Average Frame Rate: 418.0 FPS
 >>> Average Frame Time: 2.39 ms
 >>> Pixel Fill Rate   : 385.2 Mpixels/sec
--------------------------------------------------

[PASS] GPU performance exceeds hardware acceleration threshold!
```

### 4.4 性能调优建议
随身 WiFi 默认的 CPU 调频策略常为较为迟钝的 `conservative`。若作为常驻微服务器，推荐将 Governor 切换为 `ondemand`，可在保持待机低功耗的同时，实现突发计算负载时的毫秒级主频拉满：
```bash
for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    echo ondemand | sudo tee $g
done
```

---

## 📁 5. 仓库结构 (Repository Structure)

```text
msm8916-adreno306-headless-driver/
├── README.md                  # 本文档 (完整技术方案、背景、基准测试与指南)
├── bin/                       # 适用于 Linux 5.15 arm64 的预编译内核模块
│   ├── gpu-sched.ko           # DRM GPU 调度器内核模块 (~41 KB)
│   └── msm.ko                 # 修复补丁后的 Adreno/MSM DRM 驱动模块 (~1.0 MB)
├── patches/                   # 源码补丁文件
│   └── 0001-msm-adreno-headless-gpu-support.patch  # git 格式完整源码补丁
└── scripts/                   # 测试与自动化脚本
    ├── install_gpu_driver.sh  # OpenStick 一键安装部署脚本
    ├── test_adreno_3d.py      # EGL 离屏硬件 3D 渲染与显存回读测试脚本
    └── benchmark_gpu.py       # 硬件 GPU 帧率 (FPS) 与像素填充率跑分脚本
```

---

## 🚀 6. 安装与使用指南 (Installation & Quick Start)

### 方式 A：一键部署（推荐）
如果你使用的是与预编译模块兼容的 Linux 5.15 arm64 内核（如 OpenStick 官方 Debian 镜像），直接执行一键安装脚本：

```bash
# 赋予执行权限并以 root 运行
sudo chmod +x scripts/*.sh scripts/*.py
sudo ./scripts/install_gpu_driver.sh
```

### 方式 B：手动安装步骤
1. **复制模块到系统内核目录**：
   ```bash
   sudo cp bin/gpu-sched.ko /lib/modules/$(uname -r)/kernel/drivers/gpu/drm/
   sudo cp bin/msm.ko /lib/modules/$(uname -r)/kernel/drivers/gpu/drm/
   sudo depmod -a
   ```
2. **设置开机自动加载**：
   ```bash
   echo -e "gpu-sched\nmsm" | sudo tee /etc/modules-load.d/gpu.conf
   ```
3. **即时加载模块**：
   ```bash
   sudo modprobe gpu-sched
   sudo modprobe msm
   ```
4. **确保固件已就绪**：
   确保 `/lib/firmware/qcom/` 目录下存在 `a300_pm4.fw` 和 `a300_pfp.fw`。
   若缺失，可通过安装 `firmware-qcom` 软件包获取：
   ```bash
   sudo apt-get install -y firmware-qcom
   ```

### 运行硬件 3D 加速测试
安装 Mesa 用户态图形库，然后运行测试脚本：
```bash
sudo apt-get install -y python3 libdrm-freedreno1 libgbm1 libegl1-mesa libgles2
python3 scripts/test_adreno_3d.py
```
若看到 `[SUCCESS] 3D GPU RENDERING PIPELINE IS 100% OPERATIONAL!` 则表示硬件显卡驱动完全正常工作。

---

## 🛠️ 7. 从源码编译驱动 (Building from Source)

如果你使用的是其他版本的 Linux 内核（例如 6.1 LTS 或 6.6 LTS），可以参考以下步骤自行编译：

1. **下载对应内核源码**：
   ```bash
   git clone --depth=1 -b linux-5.15.y https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git
   cd linux
   ```

2. **打入无头补丁**：
   ```bash
   git apply /path/to/patches/0001-msm-adreno-headless-gpu-support.patch
   ```

3. **导入当前板子的内核配置与符号表**：
   ```bash
   # 从运行中的板子上复制当前配置
   scp root@<dongle-ip>:/boot/config-$(uname -r) .config
   scp root@<dongle-ip>:/boot/System.map-$(uname -r) Module.symvers
   ```

4. **调整驱动依赖配置（关键！）**：
   在随身 WiFi 内核中，通常未导出 MIPI DSI 符号。为防止 `modpost: undefined symbol mipi_dsi_*` 错误，需在 `.config` 中禁用 DSI 和 DP 控制器驱动：
   ```text
   CONFIG_DRM_MSM=m
   CONFIG_DRM_MSM_GPU_STATE=y
   # CONFIG_DRM_MSM_DSI is not set
   # CONFIG_DRM_MSM_DP is not set
   CONFIG_DRM_SCHED=m
   ```
   同步配置头文件：
   ```bash
   make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- syncconfig
   ```

5. **编译模块**：
   ```bash
   make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j$(nproc) M=drivers/gpu/drm modules
   ```
   编译完成后，即可在 `drivers/gpu/drm/msm/msm.ko` 和 `drivers/gpu/drm/scheduler/gpu-sched.ko` 获得驱动二进制。

---

## 🎮 8. 随身 WiFi 无头显卡能玩什么？(Use Cases & Applications)

许多人好奇：“随身 WiFi 又没有屏幕，驱动了显卡有什么用？”  
实际上，现代 GPU 在服务器端和无头环境下的用途极其广泛：

1. **VNC / RDP 远程桌面 3D 硬件加速 (VirtualGL / TurboVNC)**：
   借助 `/dev/dri/renderD128`，可以在轻量级 X11 / Wayland 桌面上使用物理 Adreno 306 渲染 3D 图形与窗口合成特效，彻底告别由 CPU 软解渲染导致的 100% 满载卡顿。
2. **EGL 离屏 3D 图形渲染 (Offscreen Rendering)**：
   通过 EGL Surfaceless 扩展，直接在物理 VRAM 显存中渲染 3D 场景、图表或动画，并输出为图片或视频流。
3. **图像与视频着色器处理 (OpenGL ES Compute & Shaders)**：
   利用 Adreno 306 统一着色器架构并行处理图像卷积滤镜、边缘检测或视频帧特效。
4. **Headless WebGL 自动化与爬虫渲染**：
   运行无头浏览器（Headless Chromium / Puppeteer），开启 `--use-gl=egl` 即可获得真实的硬件 WebGL 加速支持，用于复杂 Web 3D 页面渲染与反爬测试。
5. **极低功耗边缘渲染服务器**：
   整机功耗仅 1W~1.5W，是一台随时随地插在移动电源或 USB 接口上的超迷你 GPU 边缘节点。

---

## 🔮 9. 探索更多宝藏：MSM8916 隐藏硬件全景与高阶玩法 (Hardware Deep-Dive & Advanced Capabilities)

除了已被本项目成功唤醒的 **Adreno 306 3D GPU** 之外，高通骁龙 410 (MSM8916) 随身 WiFi 板载还深藏着诸多未被发掘的强悍硬件外设。经过底层硬件探测与实测验证，以下硬件外设均原生可用：

### 9.1 无线网卡原生混杂监听模式 (Wi-Fi Monitor Mode)
- **硬件芯片**：Qualcomm WCN36xx (WCN3620 / WCN3660) + `mac80211`
- **支持模式**：经 `iw list` 验证，原生支持 **`monitor`（监听模式）**、`AP`（热点模式）、`IBSS`、`AP/VLAN`。
- **高阶玩法**：
  1. **随身客流与无线探针**：无需关联任何 Wi-Fi，监听周围 50 米内所有智能手机广播的 802.11 Probe Request 报文，提取设备 MAC 地址与 RSSI 信号强度，统计周围实时客流与移动轨迹。
  2. **Pwnagotchi 随身无线安全审计**：配合 Bettercap / Aircrack-ng 抓取空中握手包，评估无线网络密码安全性。
- **快速启用命令**：
  ```bash
  # 创建独立的虚拟监听接口 mon0（不影响 wlan0 的正常连接）
  sudo iw dev wlan0 interface add mon0 type monitor
  sudo ip link set mon0 up

  # 监听空中 802.11 数据包
  sudo tcpdump -i mon0 -e -s 256 -c 20
  ```

---

### 9.2 板载蓝牙 4.0 / BLE 硬件激活 (`hci0`)
- **硬件芯片**：Qualcomm WCNSS Bluetooth SMD (`btqcomsmd` / `btqca`)
- **设备状态**：内核中 `hci0` 已原生初始化并就绪（`rfkill0/state = 1`）。
- **高阶玩法**：
  1. **全屋 BLE 设备嗅探网关**：实时监听抓取小米蓝牙温湿度计、智能手环、蓝牙防丢器广播包，将数据上报至 MQTT / Home Assistant。
  2. **反追踪 AirTag 尾随报警器**：编写轻量脚本持续嗅探周围是否有未知的 Apple AirTag / 华为 Tag 蓝牙信标连续尾随，若有则触发板载红色 LED 报警。
  3. **手机空间定位 (iBeacon 模拟器)**：将棒子作为室内精准微定位基站，iPhone / Android 靠近时自动触发家庭场景自动化。
- **快速启用命令**：
  ```bash
  sudo apt-get install -y bluez
  sudo hciconfig hci0 up
  sudo bluetoothctl scan on
  ```

---

### 9.3 高通 Venus 硬件视频编解码加速器 (`/dev/video0` & `/dev/video1`)
- **硬件芯片**：Qualcomm Venus VPU (`1d00000.video-codec`)
- **设备节点**：
  - `/dev/video0`：Qualcomm Venus 硬件视频编码器 (H.264 Encoder)
  - `/dev/video1`：Qualcomm Venus 硬件视频解码器 (H.264 Decoder)
- **高阶玩法**：
  - **1W 极低功耗 RTSP 视频流转码推流网关**：通过网络拉取监控视频流，使用 Venus VPU 芯片进行硬件 H.264 实时编解码与转码推流，**CPU 占用率低于 5%**，打造全天候超节能视频流转码盒或轻量 NVR 录像机。
- **验证命令**：
  ```bash
  sudo apt-get install -y v4l-utils
  v4l2-ctl -d /dev/video0 --all
  ```

---

### 9.4 USB OTG 虚拟设备控制中心 (ConfigFS USB Gadget)
- **控制器**：ChipIdea High-Speed USB OTG Controller (`ci_hdrc.0`)
- **高阶玩法**：
  1. **USB 虚拟外置装机光驱 (DriveDroid / CD-ROM Gadget)**：将 Windows PE、Ubuntu 或 Arch Linux 的 `.iso` 镜像存放于板载闪存，插入任意电脑后，电脑 BIOS 直接识别为一台**外置物理 USB DVD 光驱**，实现免拔插 U 盘的装机与救援。
  2. **物理 BadUSB 硬件击键自动化 (HID Gadget)**：模拟真实物理键盘与鼠标发送击键与轨迹，彻底绕过所有软件级反作弊与防脚本检测。
  3. **USB 虚拟无损声卡 (UAC1/UAC2 Audio Gadget)**：将棒子模拟为电脑外置免驱 USB 独立声卡，棒子在后台接收网络 AirPlay / DLNA 串流实现高保真音频桥接。

---

### 9.5 电子级双卡硬件切换控制器 (`sim:en` 与 `sim:sel`)
- **硬件原理**：板载集成电路中包含一颗受 GPIO 控制的模拟多路开关，直接映射在内核 LED/GPIO 控制器中：
  - `/sys/class/leds/sim:en`：SIM 模块硬件电源使能
  - `/sys/class/leds/sim:sel`：SIM 物理通道选择器（`0` = 板载贴片 eSIM，`1` = 外置 Nano SIM 卡槽）
- **高阶玩法**：
  - 无需任何机械拨动开关，通过终端命令或自动化脚本即可实现**贴片卡与外置物理卡之间的毫秒级电子通断与无缝切换**：
  ```bash
  # 切换至板载 eSIM 贴片卡
  echo 0 | sudo tee /sys/class/leds/sim:sel/brightness

  # 切换至外置物理 Nano SIM 卡槽
  echo 1 | sudo tee /sys/class/leds/sim:sel/brightness
  ```

---

### 9.6 三色可编程硬件状态 LED (`blue:wifi`, `green:internet`, `red:os`)
- **硬件节点**：`/sys/class/leds/`
- **支持触发器**：`heartbeat`（心跳呼吸灯）、`cpu`（CPU 活动）、`mmc0`（磁盘读写）、`phy0rx/tx`（无线收发）等。
- **玩法示例**：
  ```bash
  # 将红色 LED 配置为 CPU 负载呼吸心跳灯
  echo heartbeat | sudo tee /sys/class/leds/red:os/trigger

  # 将绿色 LED 绑定至 Wi-Fi 物理数据收发闪烁
  echo phy0rx | sudo tee /sys/class/leds/green:internet/trigger
  ```

---

## 📄 10. 授权协议 (License)

- 内核驱动与补丁代码遵循 [GPL-2.0 License](https://www.gnu.org/licenses/old-licenses/gpl-2.0.html)（Linux Kernel License）。
- 自动化测试与安装脚本遵循 [MIT License](https://opensource.org/licenses/MIT)。

---

## 🤝 11. 鸣谢 (Acknowledgments)

- [OpenStick 项目](https://github.com/OpenStick) 与全体社区贡献者
- Mesa Freedreno 项目开发团队
- Linux DRM / MSM 子系统维护者
