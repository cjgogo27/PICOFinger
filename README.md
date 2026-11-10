# PICOFinger

Drive a **dexterous hand** in real time with the hand tracking of a **PICO / Quest** headset.

Everything happens in one web page: open it in the headset browser and go. No APK, no Unity,
no ROS, no vendor SDK required.
The repository ships a ready-to-run reference implementation (PaXini DexH13); supporting a
different hand only means replacing the driver layer.

---

## How it works

```
PICO / Quest browser
  └─ WebXR XRHand: 25 hand joints (3D, 60-72 Hz)
       └─ WebSocket / HTTP POST
            └─ this server
                 ├─ 25 joints -> per-finger flexion (sum of joint angles)
                 ├─ auto-calibration -> normalised 0..1
                 └─ x target pose -> N joint targets
                      └─ serial / CAN / UDP -> dexterous hand
```

**Why WebXR**: the headset already tracks hands and the browser exposes the joints through
`XRHand`, so there is no app to build, sign or install. Edit a line, reload the page, done.

**Why it beats a camera**: the joints are true 3D, unaffected by perspective or lighting, at a
higher frame rate and without dropping frames.

**When there is no hand tracking**: the page falls back to the **controller trigger**, mapped
0..1 onto all fingers, so the link can be verified before the tracking is sorted out.

---

## Requirements

| Part | Requirement |
|---|---|
| Headset | any model with WebXR Hand Tracking (PICO 4 / 4 Ultra, Quest 2/3, ...) |
| Server | any Linux / Windows / macOS box with Python 3.10+, on the same LAN as the headset |
| Hand | any programmable dexterous hand; a PaXini DexH13 driver (RS485 Modbus RTU) ships here |

> Degrees of freedom, transports and enable sequences differ wildly between hands.
> Adapting one is described in "Bring your own hand" below.

---

## Quick start

```bash
pip install flask flask-sock pyserial

export DEXH13_PORT=/dev/ttyUSB0        # your serial device
python3 xr_server.py                   # http://0.0.0.0:8000
```

**WebXR insists on a secure context**: the page has to be served over HTTPS or from
`localhost`. A plain LAN http address will not work, so run it with a certificate:

```bash
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout certs/key.pem -out certs/cert.pem -days 3650 \
  -subj "/CN=<server-ip>"

export DEXH13_SSL_CERT=certs/cert.pem
export DEXH13_SSL_KEY=certs/key.pem
export DEXH13_WEB_PORT=8443
python3 xr_server.py                   # https://0.0.0.0:8443
```

The browser will complain about the self-signed certificate; accept it ("Advanced -> Proceed").
The context is still secure.

---

## On the headset

1. **Turn hand tracking on**
   - PICO: Settings -> Interaction -> **"controllers and hands switch automatically"**
     (with "controllers only" the hand input source never appears)
   - Quest: Settings -> Movement tracking -> enable **Hand tracking**
2. Put the headset and the server on the **same LAN**
3. Open `https://<server-ip>:8443/xr` in the headset browser
4. Accept the certificate warning
5. Press **Enter VR** in the middle of the page
6. Raise your hand in front of you and start controlling

### What the page shows

```
Hand: tracked / not tracked
Inputs: N sources, M with hand data
Sending: XX Hz
Render layer: ready
```

- `0 inputs with hand data` -> the system is still in controller mode; see step 1
- `not tracked` -> the hand is outside the headset's field of view
- `0 Hz` -> nothing is being sent, see the next section

The page DOM is not visible inside an immersive session, so the page also sends a heartbeat
once a second; the server exposes `hb_age`, `age`, `ev` and `err` on `/api/status` for
troubleshooting.

---

## Endpoints

| Path | Purpose |
|---|---|
| `/xr` | headset page (WebXR + WebSocket) |
| `/ws/xr` | WebSocket channel |
| `/api/xr_frame` | the same over HTTP POST (used when the certificate blocks wss) |
| `/api/status` | link state, frame counters, last error |
| `/api/start` / `/api/stop` | enable / disable the hand |

Joint order follows the WebXR spec: `wrist`, `thumb-metacarpal`, `thumb-phalanx-proximal`, ...,
`pinky-finger-tip`, 25 joints in total.

---

## Bring your own hand

Only the `Hand` class in `xr_server.py` is hardware specific; everything else is generic:

```python
class Hand:
    def open(self):    ...   # open the transport (serial / CAN / UDP)
    def enable(self):  ...   # power on (many hands need a fixed sequence)
    def disable(self): ...   # power off
    def send(self, targets): ...   # push N joint targets
    def version(self): ...   # firmware version, only used for logging
```

To adapt it:

1. Map the **normalised ratios** (0..1 per finger) coming out of `Retargeter` onto your hand's
   joint units (degrees / encoder counts / current). In the reference implementation `FIST`
   holds the 13 motor targets for "make a fist"; the ratio simply scales them.
2. Drop `protocol.py` (Modbus) and plug in your own transport.
3. Different DoF counts are fine: `MOTOR_FINGER` says which finger joint *i* belongs to.
   Hands without a pinky or without a wrist (a 4-finger, 13-DoF hand, for instance) simply
   ignore the extra joints.

---

## Troubleshooting

**Endless spinner after pressing Enter VR (PICO)**

WebXR only composes a frame if the page submits one. The page must:

1. set `baseLayer` with `new XRWebGLLayer(session, gl)`
2. bind the framebuffer, clear it and flush **every** frame

Also, the `session` referenced inside the `requestAnimationFrame` callback has to be a
module-scope variable; otherwise the first frame throws a `ReferenceError` and the spinner
stays forever. Both are handled here.

**No hand data**

- interaction mode is "controllers only" -> the hand input source never appears
- the hand is outside the tracking volume
- some OS versions only accept `hand-tracking` as an **optional** feature; putting it in
  `requiredFeatures` makes the session request hang

**WebSocket refuses to connect (page on https, wss blocked by the self-signed cert)**

Browsers usually scope the certificate exception to `https://` and not to `wss://`. The page
falls back to `POST /api/xr_frame` on its own, and a USB cable removes the problem entirely:

```bash
adb reverse tcp:8443 tcp:8443
# open http://localhost:8443/xr in the headset
```

**Stiff or jittery motion**

- spend a few seconds opening and closing the hand so the auto-calibration learns the range
- lower `Retargeter(alpha=)` for smoother output
- the per-frame rate limit lives in `update()` (400 units per frame by default)

---

## Known limitations

- joint poses only: no hand mesh, no haptics; pinch and fist have to be derived
- joints return `null` when the hand leaves the field of view; pick a hold or return-to-zero policy
- `hand-tracking` support varies between headset OS versions, so always detect at runtime
  instead of sniffing the user agent

---

## Tested with

| Item | Value |
|---|---|
| Headset | PICO 4 Ultra (browser, WebXR) |
| Hand | PaXini DexH13 (4 fingers, 13 DoF, RS485 Modbus RTU at 460800 or 4 Mbaud, right hand `0x79`) |
| Server | Ubuntu 22.04, Python 3.10 |

---

## License

MIT