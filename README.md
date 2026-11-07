# PICOFinger

用 **PICO 4 Ultra / Quest** 的手部追踪，实时驱动一只 **PaXini DexH13** 灵巧手。

不需要官方 SDK、不需要 ROS、不需要装 APK——头显打开一个网页就行。

**电脑端控制台：** https://10.16.10.28:8443/
**头显端页面：** https://10.16.10.28:8443/xr ← 头显打开这个
（均在实验室内网）

---

## 原理

```
PICO / Quest 浏览器
  └─ WebXR XRHand：25 个手部关节（3D 坐标，60–72 Hz）
       └─ WebSocket
            └─ 本服务
                 ├─ 25 关节 → 每根手指屈曲度（关节夹角之和）
                 ├─ 自动标定区间 → 归一化 0~1
                 └─ × 握拳手势的电机值 → 13 个关节目标
                      └─ RS485 / Modbus RTU 460800 → DexH13
```

**为什么用 WebXR**：头显自带手部追踪，浏览器直接调 `XRHand`，免去装 APK、Unity、签名一整套。

**为什么比摄像头好**：给的是 3D 坐标，不受透视和光照影响；60+ Hz，不丢帧。

**限制**：DexH13 只有 4 指 13 自由度（拇指 4 + 食/中/无名 各 3），没有小指，也没有腕部自由度。所以 25 个关节里，小指和腕部信息用不上。

## 硬件

| 部件 | 要求 |
|---|---|
| 灵巧手 | PaXini DexH13，固件 1.4.4 实测通过 |
| 串口 | USB-RS485（PL2303GR / CH343 等），460800 8N1，从站 `0x79`（右手） |
| 头显 | PICO 4 Ultra / Quest 2/3，需支持 WebXR Hand Tracking |

## 快速开始

```bash
pip install flask flask-sock pyserial

export DEXH13_PORT=/dev/ttyUSB0
python3 xr_server.py            # 默认 http://0.0.0.0:8000
```

**WebXR 强制 HTTPS**，局域网 IP 不是安全上下文，所以生产环境要带证书：

```bash
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout certs/key.pem -out certs/cert.pem -days 3650 -subj "/CN=<你的IP>"

export DEXH13_SSL_CERT=certs/cert.pem
export DEXH13_SSL_KEY=certs/key.pem
export DEXH13_WEB_PORT=8443
python3 xr_server.py
```

## 头显操作

1. **交互方式选「仅手势」**（PICO 把"手部追踪"叫"手势"，就在 设置 → 交互 里）
2. 头显和服务器在同一局域网
3. 浏览器打开 `https://<服务器IP>:8443/xr`
4. 证书警告 → 高级 → 继续前往（自签名证书的正常现象）
5. 点「进入 VR」，允许手部追踪
6. 电脑端 `curl -X POST https://<IP>:8443/api/start` 使能电机
7. 手举到胸前，开始控制

页面会显示诊断信息：

```
手：已追踪 / 未追踪
输入源：N 个，含手部数据 M 个
发送：XX Hz
```

- `含手部数据 0 个` → 交互方式没切到「仅手势」，或浏览器不支持 XRHand
- `未追踪` → 手没在视野内

## 接口

| 路径 | 说明 |
|---|---|
| `/xr` | 头显页面（WebXR + WebSocket） |
| `/ws/xr` | 接收 25 关节，长度必须为 25 |
| `/api/status` | 当前比值、帧数、是否使能 |
| `/api/start` | 使能电机（五步时序） |
| `/api/stop` | 失能 |

关节顺序遵循 WebXR 规范：wrist, thumb-metacarpal, thumb-proximal, thumb-distal, thumb-tip, index-metacarpal, … pinky-tip。

## 已知问题

- **自签名证书**：PICO 浏览器可能比 Chrome 严格，不给"继续前往"。备选方案是 USB 接服务器做 `adb reverse tcp:8443 tcp:8443`，改开 `http://localhost:8443/xr`（localhost 算安全上下文）。
- **标定**：前几秒做几次张手/握拳，屈曲度区间会自动学习；不动的话用默认区间，响应会偏迟钝。
- **抖动**：已做低通 + 单帧限速 400，若仍抖可调 `Retargeter(alpha=)`，越小越平滑。

## 相关

- 完整项目（猜拳、跟随模仿、Flask 控制台）：[cjgogo27/DexFingerGuessing](https://github.com/cjgogo27/DexFingerGuessing)

## License

MIT