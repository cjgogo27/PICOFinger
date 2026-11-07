# PICOFinger

用 **PICO / Quest 头显的手部追踪**，实时遥操作一只**灵巧手**。

整套链路只有一个网页：头显打开浏览器就能用，不装 APK、不用 Unity、不需要 ROS，也不依赖任何厂商 SDK。
仓库里带一个可直接运行的参考实现（PaXini DexH13），换成别的灵巧手只需要替换一个驱动层。

---

## 原理

```
PICO / Quest 浏览器
  └─ WebXR XRHand：25 个手部关节（3D 坐标，60–72 Hz）
       └─ WebSocket / HTTP POST
            └─ 本仓库服务
                 ├─ 25 关节 → 每根手指屈曲度（关节夹角之和）
                 ├─ 自动标定区间 → 归一化 0~1
                 └─ × 目标姿态的电机量 → N 个关节目标
                      └─ 串口 / CAN / UDP → 灵巧手
```

**为什么用 WebXR**：头显自带手部追踪，浏览器直接通过 `XRHand` 暴露关节，省掉装应用、打包、签名整套流程。改一行代码刷新页面就生效。

**为什么比摄像头好**：给的是真 3D 坐标，不受透视和光照影响；帧率更高，不丢帧。

**没有手部追踪时**：自动回退到**手柄扳机**控制——扳机值 0~1 映射到全部手指，先让链路跑通。

---

## 硬件要求

| 部件 | 要求 |
|---|---|
| 头显 | 支持 WebXR Hand Tracking 的机型（PICO 4 / 4 Ultra、Quest 2/3 等） |
| 服务器 | 任意 Linux / Windows / macOS，能跑 Python 3.10+，与头显同一局域网 |
| 灵巧手 | 任意可编程灵巧手；仓库自带 PaXini DexH13（RS485 Modbus RTU）的参考驱动 |

> 不同灵巧手的自由度、通信方式和使能时序差异很大，**适配方法见下文「接到你自己的灵巧手」**。

---

## 快速开始

```bash
pip install flask flask-sock pyserial

export DEXH13_PORT=/dev/ttyUSB0        # 换成你的串口
python3 xr_server.py                   # 默认 http://0.0.0.0:8000
```

**WebXR 要求安全上下文**：网页必须是 HTTPS 或 `localhost`，普通局域网 http 地址不行。所以实际使用时带证书：

```bash
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout certs/key.pem -out certs/cert.pem -days 3650 \
  -subj "/CN=<服务器IP>"

export DEXH13_SSL_CERT=certs/cert.pem
export DEXH13_SSL_KEY=certs/key.pem
export DEXH13_WEB_PORT=8443
python3 xr_server.py                   # https://0.0.0.0:8443
```

自签名证书浏览器会警告，点「高级 → 继续前往」即可（安全上下文仍然成立）。

---

## 头显操作

1. **打开手部追踪**
   - PICO：设置 → 交互 → 选「**手柄手势自动切换**」（选「仅手柄」时手部输入源根本不会出现）
   - Quest：设置 → 动作追踪 → 开启「手部追踪」
2. 头显与服务器连**同一个局域网**
3. 浏览器打开 `https://<服务器IP>:8443/xr`
4. 接受证书警告
5. 点页面中间的「**进入 VR**」
6. 手举到胸前，开始控制

### 页面显示什么

```
手：已追踪 / 未追踪
输入源：N 个，含手部数据 M 个
发送：XX Hz
渲染层：已建立
```

- `含手部 0 个` → 系统还是手柄模式，按上面第 1 步切换
- `未追踪` → 手不在头显视野内
- `发送 0 Hz` → 数据没发出去，看下一节

进 VR 后网页的 DOM 默认不显示（所以上面这些字在头显里看不到），页面会每秒发一次心跳，服务器端 `/api/status` 能看到 `hb_age`、`age`、`ev`、`err`，用于排错。

---

## 接口

| 路径 | 说明 |
|---|---|
| `/xr` | 头显页面（WebXR + WebSocket） |
| `/ws/xr` | WebSocket 通道 |
| `/api/xr_frame` | 同上的 HTTP POST 后备（证书挡住 wss 时用） |
| `/api/status` | 链路状态、帧数、错误 |
| `/api/start` / `/api/stop` | 使能 / 失能灵巧手 |

关节顺序遵循 WebXR 规范：`wrist`、`thumb-metacarpal`、`thumb-phalanx-proximal`、…、`pinky-finger-tip`，共 25 个。

---

## 接到你自己的灵巧手

`xr_server.py` 里只有 `Hand` 这一类跟具体硬件相关，其余都是通用的：

```python
class Hand:
    def open(self):    ...   # 打开通信（串口 / CAN / UDP）
    def enable(self):  ...   # 上电使能（很多手需要特定的时序）
    def disable(self): ...   # 失能
    def send(self, targets): ...   # 下发 N 个关节目标
    def version(self): ...   # 读取固件版本，仅用于日志
```

替换步骤：

1. 把 `Retargeter` 输出的**归一化比值**（0~1，按手指）映射成你手上的**关节目标单位**（角度 / 编码器值 / 电流）。
   参考实现里 `FIST = [...]` 就是「握拳」时 13 个电机的目标值，比值直接乘上去。
2. 关掉 `protocol.py`（Modbus）换成你的通信实现。
3. 自由度不同也没关系：`MOTOR_FINGER` 描述「第 i 个关节属于哪根手指」，按你的手改这张表即可。没有小指、没有腕部自由度的手（例如 4 指 13 自由度）会自动忽略多余关节。

---

## 常见问题

**进 VR 后一直转圈（PICO 的白色加载圈）**

WebXR 要求**每帧向 XR 帧缓冲提交一帧**，否则系统会一直等。页面必须：

1. 用 `new XRWebGLLayer(session, gl)` 设置 `baseLayer`
2. 每帧 `gl.bindFramebuffer(...)` + `gl.clear(...)` + `gl.flush()`

另外 `requestAnimationFrame` 的回调里引用的 `session` 必须是**模块级变量**，否则第一帧就抛 `ReferenceError`，表现同样是永远转圈。这两点都已在本仓库实现。

**拿不到手部数据**

- 头显交互方式是「仅手柄」→ 手部输入源不会出现
- 头显不在手部追踪的视野内
- 部分系统版本的 `hand-tracking` 只能作**可选特性**（`optionalFeatures`），放 `requiredFeatures` 会挂起

**WebSocket 连不上（页面 https，wss 被自签证书挡住）**

浏览器的证书例外通常只覆盖 `https://`，不覆盖 `wss://`。页面会自动降级为 `POST /api/xr_frame`，也可用 USB 方式彻底绕开：

```bash
adb reverse tcp:8443 tcp:8443
# 头显打开 http://localhost:8443/xr
```

**手在动但很僵硬或抖动**

- 前几秒多做几次张手 / 握拳，让自动标定学到区间
- 调 `Retargeter(alpha=)`，越小越平滑
- 单帧限速在 `update()` 里（默认 400 单位/帧）

---

## 已知限制

- 只有关节姿态，没有手部网格和触觉反馈；捏合、握拳等语义要自己算
- 手出视野时关节姿态返回 `null`，需自行处理保持/回零策略
- 不同头显系统版本对 `hand-tracking` 的支持程度不同，务必做运行时特性检测，不要靠 User-Agent 判断

---

## 实测环境

| 项 | 值 |
|---|---|
| 头显 | PICO 4 Ultra（WebXR，浏览器内） |
| 灵巧手 | PaXini DexH13（4 指 13 自由度，RS485 Modbus RTU 460800，右手从站 0x79） |
| 服务器 | Ubuntu 22.04，Python 3.10 |

---

## License

MIT