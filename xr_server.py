#!/usr/bin/env python3
"""PICOFinger —— PICO / Quest 手部追踪 → PaXini DexH13 灵巧手

最小自包含实现：
  * 头显浏览器打开 /xr，读 WebXR XRHand 的 25 个关节
  * WebSocket 推给本服务
  * 重定向成 13 个电机目标
  * RS485 / Modbus RTU 下发到灵巧手

不用官方 SDK、不用 ROS、不用装 APK。

运行：
    export DEXH13_PORT=/dev/ttyUSB0
    python3 xr_server.py                # http://<ip>:8000
    # WebXR 需要 HTTPS，生产用自签证书：
    export DEXH13_SSL_CERT=certs/cert.pem DEXH13_SSL_KEY=certs/key.pem
    export DEXH13_WEB_PORT=8443
    python3 xr_server.py                # https://<ip>:8443
"""
from __future__ import annotations

import json
import math
import os
import sys
import threading
import time

from flask import Flask, Response, jsonify, render_template_string

try:
    from flask_sock import Sock
except ImportError:
    Sock = None

import protocol as P

PORT = int(os.environ.get("DEXH13_WEB_PORT", "8000"))
SERIAL_PORT = os.environ.get("DEXH13_PORT", "/dev/ttyUSB0")
BAUD = int(os.environ.get("DEXH13_BAUD", "460800"))
ADDR = int(os.environ.get("DEXH13_ADDR", "121"))     # 右手 0x79

# ---------------- 重定向 ----------------
FINGER_TRIPLETS = {
    "thumb":  [(1, 2, 3), (2, 3, 4)],
    "index":  [(5, 6, 7), (6, 7, 8), (7, 8, 9)],
    "middle": [(10, 11, 12), (11, 12, 13), (12, 13, 14)],
    "ring":   [(15, 16, 17), (16, 17, 18), (17, 18, 19)],
    "pinky":  [(20, 21, 22), (21, 22, 23), (22, 23, 24)],
}
FINGER_ORDER = ["thumb", "index", "middle", "ring", "pinky"]
MOTOR_FINGER = (["thumb"] * 4) + (["index"] * 3) + (["middle"] * 3) + (["ring"] * 3)
FIST = [3925, 3688, 3800, 3451, 3569, 3800, 3406, 4000, 3800, 4500, 4300, 2000, 2044]
MIN_SPAN = 60.0


def _angle(a, b, c):
    v1 = [a[i] - b[i] for i in range(3)]
    v2 = [c[i] - b[i] for i in range(3)]
    n1 = math.sqrt(sum(x * x for x in v1))
    n2 = math.sqrt(sum(x * x for x in v2))
    if n1 < 1e-9 or n2 < 1e-9:
        return 180.0
    cos = sum(x * y for x, y in zip(v1, v2)) / (n1 * n2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


class Retargeter:
    def __init__(self, alpha=0.35):
        self.lo = {n: None for n in FINGER_ORDER}
        self.hi = {n: None for n in FINGER_ORDER}
        self.sm = {n: 0.0 for n in FINGER_ORDER}
        self.alpha = alpha
        self.last = [0] * 13

    def update(self, joints):
        f = {}
        for n, trips in FINGER_TRIPLETS.items():
            f[n] = sum(180.0 - _angle(joints[a], joints[b], joints[c]) for a, b, c in trips)
        for n in FINGER_ORDER:
            self.lo[n] = f[n] if self.lo[n] is None else min(self.lo[n], f[n])
            self.hi[n] = f[n] if self.hi[n] is None else max(self.hi[n], f[n])
        out = {}
        for n in FINGER_ORDER:
            span = self.hi[n] - self.lo[n]
            r = (f[n] - 10.0) / 250.0 if span < MIN_SPAN else (f[n] - self.lo[n]) / span
            r = max(0.0, min(1.0, r))
            self.sm[n] = (1 - self.alpha) * self.sm[n] + self.alpha * r
            out[n] = self.sm[n]
        tgt = [int(round(self.sm[fg] * FIST[i])) for i, fg in enumerate(MOTOR_FINGER)]
        tgt = [max(self.last[i] - 400, min(self.last[i] + 400, t)) for i, t in enumerate(tgt)]
        tgt = [max(-1000, min(5000, t)) for t in tgt]
        self.last = tgt
        return tgt, out


# ---------------- 灵巧手 ----------------
class Hand:
    def __init__(self):
        self.ser = None
        self.lock = threading.Lock()
        self.enabled = False

    def open(self):
        import serial
        self.ser = serial.Serial(SERIAL_PORT, BAUD, timeout=0.15, write_timeout=1.0)
        return self

    def enable(self):
        with self.lock:
            P.write_regs(self.ser, ADDR, P.REG_ENABLE, [0]); time.sleep(0.2)
            P.write_regs(self.ser, ADDR, P.REG_ENABLE, [1]); time.sleep(0.2)
            P.write_regs(self.ser, ADDR, P.REG_MODE, [3] * 13); time.sleep(0.2)
            P.write_regs(self.ser, ADDR, P.REG_CMD, [6] * 13); time.sleep(2.0)
            P.write_regs(self.ser, ADDR, P.REG_CMD, [0] * 13)
            self.enabled = True

    def disable(self):
        with self.lock:
            P.write_regs(self.ser, ADDR, P.REG_CMD, [7] * 13)
            self.enabled = False

    def send(self, targets):
        with self.lock:
            P.write_regs(self.ser, ADDR, P.REG_TARGET, targets)

    def version(self):
        with self.lock:
            regs = P.read_regs(self.ser, ADDR, 0x1000, 14)
        raw = b"".join(r.to_bytes(2, "big") for r in regs)
        return raw.split(b"\x00")[0].decode("ascii", "ignore").strip()


# ---------------- 状态 ----------------
LOCK = threading.Lock()
STATE = {"t": 0.0, "ratios": {}, "frames": 0, "active": False, "msg": ""}
RETARGET = Retargeter()
HAND = None

app = Flask(__name__)
sock = Sock(app) if Sock else None

PAGE = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "xr.html"),
            encoding="utf-8").read()


@app.route("/xr")
def xr_page():
    return Response(PAGE, mimetype="text/html; charset=utf-8")


@app.route("/")
def index():
    return Response("<meta charset=utf-8><h3>PICOFinger</h3>"
                    "<p>头显打开 <b>/xr</b>，本机用 /api/status 看状态。</p>",
                    mimetype="text/html; charset=utf-8")


@app.route("/api/status")
def status():
    with LOCK:
        s = dict(STATE)
    s["age"] = round(time.time() - STATE["t"], 2) if STATE["t"] else None
    s["enabled"] = bool(HAND and HAND.enabled)
    return jsonify(s)


@app.route("/api/start", methods=["POST"])
def start():
    threading.Thread(target=HAND.enable, daemon=True).start()
    with LOCK:
        STATE["active"] = True
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def stop():
    threading.Thread(target=HAND.disable, daemon=True).start()
    with LOCK:
        STATE["active"] = False
    return jsonify({"ok": True})


if sock:
    @sock.route("/ws/xr")
    def ws_xr(ws):
        while True:
            msg = ws.receive()
            if msg is None:
                break
            try:
                d = json.loads(msg)
                joints = d.get("hand")
                if not joints or len(joints) != 25:
                    continue
                tgt, ratios = RETARGET.update(joints)
                with LOCK:
                    STATE["t"] = time.time()
                    STATE["ratios"] = {k: round(v, 3) for k, v in ratios.items()}
                    STATE["frames"] += 1
                if HAND and HAND.enabled:
                    HAND.send(tgt)
            except Exception:
                continue


def main():
    global HAND
    HAND = Hand()
    try:
        HAND.open()
        print("灵巧手已连接：", HAND.version())
    except Exception as e:
        print("灵巧手连接失败（只看不动）：", e)
        HAND = None

    cert = os.environ.get("DEXH13_SSL_CERT")
    key = os.environ.get("DEXH13_SSL_KEY")
    if cert and key:
        print("https://0.0.0.0:%d  (/xr 供头显访问)" % PORT)
        app.run(host="0.0.0.0", port=PORT, threaded=True,
                ssl_context=(cert, key), use_reloader=False)
    else:
        print("http://0.0.0.0:%d  （WebXR 需要 HTTPS，见 README）" % PORT)
        app.run(host="0.0.0.0", port=PORT, threaded=True, use_reloader=False)


if __name__ == "__main__":
    sys.exit(main())