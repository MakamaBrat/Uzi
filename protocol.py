# -*- coding: utf-8 -*-
"""Chison wireless probe protocol helpers (UDP)."""
from __future__ import annotations

import struct
from typing import Optional, Tuple

# Network defaults from protocol doc
SENSOR_IP = "192.168.0.188"
SENSOR_PORT = 9000
LOCAL_PORT = 9000
MAX_DATAGRAM = 1400

# Heartbeat header (13 bytes) + 1024 payload bank = 1037 total
HEARTBEAT_LEN = 1037
HEARTBEAT_HEADER = bytes([
    0x01, 0x55, 0xAA,
    0x12, 0x34, 0x56, 0x78, 0x9A,
    0xFF, 0xC2, 0xFF, 0x03, 0x00,
])

# Wake byte
WAKE_BYTE = b"a"  # 0x61


def next_crc(data: int, crc: int) -> int:
    """Custom 8-bit CRC from FreeTest.nextCRC (Java port)."""
    p1, p2 = data & 0xFF, crc & 0xFF
    v0 = p1 >> 7
    v1 = p1 >> 6
    v2 = v0 ^ v1
    v3 = v2 ^ p1 ^ p2
    v4 = p2 >> 6
    v3 ^= v4
    v5 = p2 >> 7
    v3 ^= v5
    v3 &= 1
    v6 = p1 >> 1
    v7 = v1 ^ v6 ^ p1 ^ p2
    v8 = p2 >> 1
    v7 ^= v8
    v7 ^= v4
    v7 &= 1
    v9 = p1 >> 2
    v10 = v1 ^ v9
    v10 ^= v6
    v10 ^= p1
    v10 ^= p2
    v10 ^= v8
    v11 = p2 >> 2
    v10 ^= v11
    v10 ^= v4
    v10 &= 1
    v12 = p1 >> 3
    v0 ^= v12
    v0 ^= v9
    v0 ^= v6
    v0 ^= v8
    v0 ^= v11
    v6 = p2 >> 3
    v0 ^= v6
    v0 ^= v5
    v0 &= 1
    v8 = p1 >> 4
    v13 = v8 ^ v12
    v9 ^= v13
    v9 ^= v11
    v9 ^= v6
    v11 = p2 >> 4
    v9 ^= v11
    v9 &= 1
    v13 = p1 >> 5
    v14 = v13 ^ v8
    v12 ^= v14
    v6 ^= v12
    v6 ^= v11
    v12 = p2 >> 5
    v6 ^= v12
    v6 &= 1
    v1 ^= v13
    v1 ^= v8
    v1 ^= v11
    v1 ^= v12
    v1 ^= v4
    v1 &= 1
    v2 ^= v13
    v2 ^= v12
    v2 ^= v4
    v2 ^= v5
    v2 &= 1
    v2 = (v2 << 7) + (v1 << 6) + (v6 << 5) + (v9 << 4) + (v0 << 3) + (v10 << 2) + (v7 << 1) + v3
    return v2 & 0xFF


def build_heartbeat(payload: Optional[bytes] = None, m_address: int = 0xC2) -> bytes:
    """Build 1037-byte heartbeat packet. payload is 1024 bytes (or padded)."""
    hdr = bytearray(HEARTBEAT_HEADER)
    hdr[9] = m_address & 0xFF
    if payload is None:
        body = bytes([0xFF] * 1024)
    else:
        body = (payload + bytes([0xFF] * 1024))[:1024]
    return bytes(hdr) + body


def build_freetest_block(
    version: str = "SonoEye P2:1",
    name: str = "P2-OPEN",
    time_stamp: int = 0,
    regedit: int = 0,
    tenancy: int = -1,
    probe_id: str = "3",
    m_address: int = 192,
    guid: bytes = b"\x00" * 32,
    mark: int = 0,
) -> bytes:
    """
    Build freeTest 1037-byte block (always-valid: tenancy=-1).
    Layout matches FreeTest.sendUpdatedData Java.
    """
    block = bytearray([0xFF] * 1037)
    block[0:13] = [
        0x01, 0x55, 0xAA,
        0x12, 0x34, 0x56, 0x78, 0x9A,
        0xFF, m_address & 0xFF, 0xFF, 0x03, 0x00,
    ]
    buf = bytearray(257)
    pos = 0

    def write_str(s: str) -> None:
        nonlocal pos
        for c in s:
            if pos >= 256:
                return
            buf[pos] = ord(c) & 0xFF
            pos += 1

    def write_byte(b: int) -> None:
        nonlocal pos
        if pos < 256:
            buf[pos] = b & 0xFF
            pos += 1

    write_str(version)
    write_byte(59)  # ;
    write_str(name)
    write_byte(59)
    write_str(str(time_stamp))
    write_byte(59)
    write_str(f"[{regedit},{tenancy}]")
    write_byte(59)
    write_str(probe_id)
    write_byte(59)
    write_byte(58)  # :
    write_byte(59)
    buf[222] = mark & 0xFF
    g = (guid + b"\x00" * 32)[:32]
    for i in range(32):
        buf[223 + i] = g[i]
    crc = 0
    for i in range(256):
        crc = next_crc(buf[i], crc)
    buf[256] = crc
    block[13:13 + 257] = buf
    return bytes(block)


def try_parse_image_header(data: bytes) -> Optional[dict]:
    """
    Best-effort parse of incoming datagram header.
    Protocol fields are documented at a high level; exact offsets
    may vary by firmware — we detect plausible structure.
    """
    if len(data) < 16:
        return None
    info = {
        "length": len(data),
        "raw_head": data[:32].hex() if len(data) >= 32 else data.hex(),
    }
    # Heuristic: look for common sync patterns
    if data[0:2] == b"\x55\xaa" or data[0:2] == b"\xaa\x55":
        info["sync"] = data[0:2].hex()
    return info
