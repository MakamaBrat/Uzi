# -*- coding: utf-8 -*-
"""Chison wireless probe: UDP-константы и вспомогательные функции."""
from __future__ import annotations

from typing import Optional

SENSOR_IP = "192.168.0.188"
SENSOR_PORT = 9000
LOCAL_PORT = 9000
MAX_DATAGRAM = 1400

HEARTBEAT_LEN = 1037  # 13 байт заголовок + 1024 байта полезной нагрузки
HEARTBEAT_HEADER = bytes([
    0x01, 0x55, 0xAA,
    0x12, 0x34, 0x56, 0x78, 0x9A,
    0xFF, 0xC2, 0xFF, 0x03, 0x00,
])
WAKE_BYTE = b"a"


def build_heartbeat(payload: Optional[bytes] = None, m_address: int = 0xC2) -> bytes:
    """Обычный heartbeat на 1037 байт."""
    hdr = bytearray(HEARTBEAT_HEADER)
    hdr[9] = m_address & 0xFF
    body = (payload or b"") + bytes([0xFF] * 1024)
    return bytes(hdr) + body[:1024]


def try_parse_image_header(data: bytes) -> Optional[dict]:
    """Грубый разбор заголовка датаграммы (точные смещения зависят от прошивки)."""
    if len(data) < 16:
        return None
    info = {"length": len(data), "raw_head": data[:32].hex()}
    if data[:2] in (b"\x55\xaa", b"\xaa\x55"):
        info["sync"] = data[:2].hex()
    return info
