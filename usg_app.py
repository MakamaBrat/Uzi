#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chison USG — программа подключения к УЗИ-датчику

Режимы:
  wifi  — беспроводной датчик (UDP 192.168.0.188:9000), heartbeat + приём кадров
  usb   — USB/UVC (как веб-камера), OpenCV VideoCapture
  auto  — сначала Wi‑Fi, при неудаче USB

Лицензия: always-valid (любое устройство принимается).

Управление в окне:
  s         — снимок
  r         — запись вкл/выкл
  h         — принудительный heartbeat (Wi‑Fi)
  q / Esc   — выход
"""
from __future__ import annotations

import argparse
import os
import socket
import struct
import sys
import threading
import time
from collections import deque
from datetime import datetime
from typing import Deque, Optional, Tuple

import numpy as np

try:
    import cv2
except ImportError:
    print("Установите зависимости: pip install -r requirements.txt")
    sys.exit(1)

from protocol import (
    SENSOR_IP,
    SENSOR_PORT,
    LOCAL_PORT,
    MAX_DATAGRAM,
    WAKE_BYTE,
    build_heartbeat,
    build_freetest_block,
    try_parse_image_header,
)
from license_bypass import decode_key, set_probe_valid


# ---------------------------------------------------------------------------
# Wi-Fi transport
# ---------------------------------------------------------------------------
class WifiProbe:
    def __init__(
        self,
        sensor_ip: str = SENSOR_IP,
        sensor_port: int = SENSOR_PORT,
        local_port: int = LOCAL_PORT,
        heartbeat_interval: float = 2.0,
    ):
        self.sensor_ip = sensor_ip
        self.sensor_port = sensor_port
        self.local_port = local_port
        self.heartbeat_interval = heartbeat_interval
        self.sock: Optional[socket.socket] = None
        self._rx_thread: Optional[threading.Thread] = None
        self._hb_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.packets: Deque[bytes] = deque(maxlen=256)
        self.stats = {"rx": 0, "bytes": 0, "last_len": 0, "errors": 0}
        self.connected = False
        self.last_packet_time = 0.0

    def open(self) -> bool:
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2 * 1024 * 1024)
            except OSError:
                pass
            self.sock.bind(("0.0.0.0", self.local_port))
            self.sock.settimeout(0.3)
            self._stop.clear()
            self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
            self._hb_thread = threading.Thread(target=self._hb_loop, daemon=True)
            self._rx_thread.start()
            self._hb_thread.start()
            self._send_wake_and_heartbeat()
            self.connected = True
            return True
        except OSError as e:
            print(f"[WiFi] Не удалось открыть сокет: {e}")
            self.close()
            return False

    def _send_wake_and_heartbeat(self) -> None:
        if not self.sock:
            return
        try:
            self.sock.sendto(WAKE_BYTE, (self.sensor_ip, self.sensor_port))
            # always-valid freeTest block + classic heartbeat
            ft = build_freetest_block(
                name="P2-OPEN",
                time_stamp=int(time.time()),
                regedit=int(time.time()),
                tenancy=-1,
            )
            self.sock.sendto(ft, (self.sensor_ip, self.sensor_port))
            hb = build_heartbeat()
            self.sock.sendto(hb, (self.sensor_ip, self.sensor_port))
        except OSError as e:
            self.stats["errors"] += 1
            print(f"[WiFi] send error: {e}")

    def force_heartbeat(self) -> None:
        self._send_wake_and_heartbeat()

    def _hb_loop(self) -> None:
        while not self._stop.is_set():
            self._send_wake_and_heartbeat()
            self._stop.wait(self.heartbeat_interval)

    def _rx_loop(self) -> None:
        while not self._stop.is_set() and self.sock:
            try:
                data, addr = self.sock.recvfrom(MAX_DATAGRAM + 64)
                if not data:
                    continue
                self.packets.append(data)
                self.stats["rx"] += 1
                self.stats["bytes"] += len(data)
                self.stats["last_len"] = len(data)
                self.last_packet_time = time.time()
            except socket.timeout:
                continue
            except OSError:
                if not self._stop.is_set():
                    self.stats["errors"] += 1
                break

    def pop_packet(self) -> Optional[bytes]:
        try:
            return self.packets.popleft()
        except IndexError:
            return None

    def is_alive(self, timeout: float = 5.0) -> bool:
        if not self.connected:
            return False
        if self.last_packet_time == 0:
            return True  # waiting for first packet
        return (time.time() - self.last_packet_time) < timeout

    def close(self) -> None:
        self._stop.set()
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
        self.connected = False


# ---------------------------------------------------------------------------
# Frame reconstruction (best-effort from datagram stream)
# ---------------------------------------------------------------------------
class FrameAssembler:
    """
    Collects UDP payloads into a displayable grayscale image.
    Without full line-header map from firmware, we treat sequential
    datagrams as scanline chunks and reshape into a scrolling B-mode view.
    """

    def __init__(self, width: int = 512, height: int = 512):
        self.width = width
        self.height = height
        self.canvas = np.zeros((height, width), dtype=np.uint8)
        self.row = 0
        self.frames_ready = 0

    def feed(self, data: bytes) -> Optional[np.ndarray]:
        if len(data) < 8:
            return None
        # Skip possible small control packets
        if len(data) < 64:
            return None
        # Use payload after a soft header guess (first 16–32 bytes often meta)
        offset = 16 if len(data) > 32 else 0
        payload = np.frombuffer(data[offset:], dtype=np.uint8)
        if payload.size == 0:
            return None

        # Write as consecutive pixels, wrap rows
        flat = self.canvas.ravel()
        n = flat.size
        idx = self.row * self.width
        for i in range(0, payload.size, self.width):
            chunk = payload[i : i + self.width]
            end = idx + chunk.size
            if end <= n:
                flat[idx:end] = chunk
            else:
                # wrap
                first = n - idx
                flat[idx:] = chunk[:first]
                flat[: chunk.size - first] = chunk[first:]
            idx = (idx + self.width) % n
            self.row = (self.row + 1) % self.height
        self.frames_ready += 1
        return self.canvas.copy()


# ---------------------------------------------------------------------------
# USB / UVC path
# ---------------------------------------------------------------------------
def open_usb(index: int, width: int = 0, height: int = 0) -> Optional[cv2.VideoCapture]:
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        return None
    if width:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    if height:
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    ok, _ = cap.read()
    if not ok:
        cap.release()
        return None
    return cap


def list_usb(max_index: int = 8):
    print("USB/UVC устройства:")
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            ok, _ = cap.read()
            if ok:
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                print(f"  [{i}] {w}x{h}")
                found.append(i)
        cap.release()
    if not found:
        print("  не найдены")
    return found


# ---------------------------------------------------------------------------
# Main UI loop
# ---------------------------------------------------------------------------
def run(
    mode: str,
    sensor_ip: str,
    device: int,
    width: int,
    height: int,
    output: str,
    hb_interval: float,
):
    os.makedirs(output, exist_ok=True)

    # License: always OK for any device
    lic = decode_key("OPEN", "P2-OPEN", rent=False)
    probe = set_probe_valid("P2-OPEN", "P2-OPEN")
    print(f"[License] {lic.result} tenancy={lic.m_tenancy} (bypass={lic.bypass})")
    print(f"[Probe]   {probe['result']} tenancy={probe['pi']['tenancy']}")

    wifi: Optional[WifiProbe] = None
    usb: Optional[cv2.VideoCapture] = None
    assembler = FrameAssembler(width=width or 512, height=height or 512)

    if mode in ("wifi", "auto"):
        print(f"[WiFi] Подключение к {sensor_ip}:{SENSOR_PORT}, локальный порт {LOCAL_PORT}...")
        wifi = WifiProbe(sensor_ip=sensor_ip, heartbeat_interval=hb_interval)
        if wifi.open():
            print("[WiFi] Сокет открыт, heartbeat отправляется. Ожидание пакетов от датчика...")
            print("       Убедитесь, что ПК в сети 192.168.0.x (точка доступа датчика).")
        else:
            wifi = None
            if mode == "wifi":
                print("[WiFi] Не удалось. Выход.")
                return

    if wifi is None and mode in ("usb", "auto"):
        print(f"[USB] Открытие устройства {device}...")
        usb = open_usb(device, width, height)
        if usb is None:
            print("[USB] Устройство недоступно.")
            if mode == "usb":
                return
        else:
            print("[USB] OK")

    if wifi is None and usb is None:
        print("Нет активного источника. Проверьте Wi‑Fi AP датчика или USB.")
        return

    window = "Chison USG"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window, 800, 600)

    recording = False
    writer = None
    fps = 0.0
    t_prev = time.time()
    frame_count = 0
    last_frame = np.zeros((assembler.height, assembler.width), dtype=np.uint8)
    status = "waiting"

    print("Клавиши: s=снимок  r=запись  h=heartbeat  q=выход")

    try:
        while True:
            frame = None

            if wifi and wifi.connected:
                # drain a few packets per tick
                for _ in range(8):
                    pkt = wifi.pop_packet()
                    if pkt is None:
                        break
                    img = assembler.feed(pkt)
                    if img is not None:
                        frame = img
                        status = "wifi"
                if frame is None and wifi.is_alive():
                    frame = last_frame
                    status = "wifi-idle"
                elif not wifi.is_alive() and wifi.last_packet_time > 0:
                    status = "wifi-timeout"

            if frame is None and usb is not None:
                ok, bgr = usb.read()
                if ok:
                    if len(bgr.shape) == 3:
                        frame = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
                    else:
                        frame = bgr
                    status = "usb"

            if frame is None:
                frame = last_frame
            else:
                last_frame = frame

            # FPS
            frame_count += 1
            now = time.time()
            if now - t_prev >= 1.0:
                fps = frame_count / (now - t_prev)
                frame_count = 0
                t_prev = now

            # Overlay
            if len(frame.shape) == 2:
                display = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            else:
                display = frame.copy()

            lines = [
                f"FPS: {fps:4.1f}  mode: {status}",
                f"License: OK (always-valid)  tenancy: -1",
            ]
            if wifi:
                lines.append(
                    f"WiFi rx={wifi.stats['rx']} bytes={wifi.stats['bytes']} "
                    f"last={wifi.stats['last_len']}B err={wifi.stats['errors']}"
                )
            if recording:
                lines.append("REC")

            y = 22
            for i, text in enumerate(lines):
                color = (0, 0, 255) if text == "REC" else (0, 255, 0)
                cv2.putText(display, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)
                y += 22

            cv2.imshow(window, display)

            if recording and writer is not None:
                writer.write(display)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("s"):
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                path = os.path.join(output, f"usg_{ts}.png")
                cv2.imwrite(path, frame)
                print(f"Снимок: {path}")
            if key == ord("r"):
                if not recording:
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    path = os.path.join(output, f"usg_{ts}.avi")
                    h, w = display.shape[:2]
                    fourcc = cv2.VideoWriter_fourcc(*"XVID")
                    writer = cv2.VideoWriter(path, fourcc, 15.0, (w, h))
                    recording = True
                    print(f"Запись: {path}")
                else:
                    recording = False
                    if writer:
                        writer.release()
                        writer = None
                    print("Запись остановлена")
            if key == ord("h") and wifi:
                wifi.force_heartbeat()
                print("[WiFi] heartbeat отправлен")

            if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                break
    finally:
        if writer:
            writer.release()
        if wifi:
            wifi.close()
        if usb:
            usb.release()
        cv2.destroyAllWindows()
        print("Завершено.")


def main():
    p = argparse.ArgumentParser(description="Chison USG — подключение к УЗИ-датчику")
    p.add_argument(
        "-m", "--mode", choices=("auto", "wifi", "usb"), default="auto",
        help="источник: auto / wifi / usb (по умолчанию auto)",
    )
    p.add_argument("--ip", default=SENSOR_IP, help=f"IP датчика (по умолчанию {SENSOR_IP})")
    p.add_argument("-d", "--device", type=int, default=0, help="индекс USB-камеры")
    p.add_argument("--width", type=int, default=512, help="ширина кадра (сборка/USB)")
    p.add_argument("--height", type=int, default=512, help="высота кадра")
    p.add_argument("-o", "--output", default="snapshots", help="папка снимков")
    p.add_argument("--hb", type=float, default=2.0, help="интервал heartbeat, сек")
    p.add_argument("--list-usb", action="store_true", help="список USB-устройств и выход")
    args = p.parse_args()

    if args.list_usb:
        list_usb()
        return

    run(
        mode=args.mode,
        sensor_ip=args.ip,
        device=args.device,
        width=args.width,
        height=args.height,
        output=args.output,
        hb_interval=args.hb,
    )


if __name__ == "__main__":
    main()
