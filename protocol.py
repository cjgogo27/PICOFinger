"""最小 Modbus RTU 实现：够驱动 DexH13 的 13 个关节就行。"""
CRC_TABLE = None


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _frame(addr, fc, reg, values=None):
    if fc == 0x03:
        body = bytes((addr, fc, reg >> 8 & 0xFF, reg & 0xFF, values >> 8 & 0xFF, values & 0xFF))
    else:
        n = len(values)
        body = bytes((addr, fc, reg >> 8 & 0xFF, reg & 0xFF, n >> 8 & 0xFF, n & 0xFF, n * 2 & 0xFF))
        body += b"".join(int(v).to_bytes(2, "big", signed=True) for v in values)
    c = crc16(body)
    return body + bytes((c & 0xFF, c >> 8))


def read_regs(ser, addr, reg, count):
    ser.reset_input_buffer()
    ser.write(_frame(addr, 0x03, reg, count))
    ser.flush()
    hdr = ser.read(3)
    if len(hdr) < 3:
        raise IOError("no response")
    n = hdr[2]
    body = ser.read(n + 2)
    data = (hdr + body)[3:-2]
    return [int.from_bytes(data[i:i + 2], "big") for i in range(0, len(data) - 1, 2)]


def write_regs(ser, addr, reg, values):
    ser.write(_frame(addr, 0x10, reg, list(values)))
    ser.flush()
    ser.read(8)


REG_ENABLE = 0x1021
REG_MODE = 0x2010
REG_CMD = 0x2020
REG_TARGET = 0x2050