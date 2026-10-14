#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chison USG — просмотр картинки с УЗИ-датчика.

Источники:
  usb  — датчик как UVC-камера (OpenCV VideoCapture)
  wifi — UDP 192.168.0.188:9000 (wake + heartbeat, приём датаграмм)
  auto — сначала USB, если не найден — Wi-Fi

Клавиши: s — снимок, r — запись, h — heartbeat (Wi-Fi), q/Esc — выход.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
from collections import deque
from datetime import datetime
from typing import Deque, Optional

import numpy as np

try:
    import cv2
except ImportError:
    print("Установите зависимости: pip install -r requirements.txt")
    sys.exit(1)

from protocol import (
    LOCAL_PORT, MAX_DATAGRAM, SENSOR_IP, SENSOR_PORT, WAKE_BYTE, build_heartbeat,
)


# --------------------------------------------------------------------------
# Источники кадров
# --------------------------------------------------------------------------
class Source:
    name = "none"

    def open(self) -> bool:
        raise NotImplementedError

    def read(self) -> Optional[np.ndarray]:
        """Вернуть новый серый кадр или None, если нового кадра нет."""
        raise NotImplementedError

    def status(self) -> str:
        return ""

    def poke(self) -> None:
        pass

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class UsbSource(Source):
    name = "usb"

    def __init__(self, index: int, width: int = 0, height: int = 0):
        self.index, self.width, self.height = index, width, height
        self.cap: Optional[cv2.VideoCapture] = None
        self._fails = 0

    def open(self) -> bool:
        cap = cv2.VideoCapture(self.index)
        if not cap.isOpened():
            return False
        if self.width:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        if self.height:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        ok, _ = cap.read()
        if not ok:
            cap.release()
            return False
        self.cap, self._fails = cap, 0
        return True

    def read(self) -> Optional[np.ndarray]:
        if self.cap is None:
            return None
        ok, frame = self.cap.read()
        if not ok:
            self._fails += 1
            if self._fails > 30:  # потеряли устройство — пробуем переоткрыть
                self.close()
                self.open()
            return None
        self._fails = 0
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame

    def status(self) -> str:
        return f"usb[{self.index}]" + ("" if self.cap else " (нет сигнала)")

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None


class FrameAssembler:
    """Best-effort сборка кадра из датаграмм: формат заголовка строк не разобран."""

    def __init__(self, width: int = 512, height: int = 512):
        self.width, self.height = width, height
        self.canvas = np.zeros(width * height, dtype=np.uint8)
        self.pos = 0

    def feed(self, data: bytes) -> bool:
        if len(data) < 64:
            return False
        payload = np.frombuffer(data[16:], dtype=np.uint8)
        n, size = payload.size, self.canvas.size
        end = self.pos + n
        if end <= size:
            self.canvas[self.pos:end] = payload
        else:
            first = size - self.pos
            self.canvas[self.pos:] = payload[:first]
            self.canvas[:n - first] = payload[first:]
        self.pos = end % size
        return True

    def image(self) -> np.ndarray:
        return self.canvas.reshape(self.height, self.width).copy()


class WifiSource(Source):
    name = "wifi"

    def __init__(self, ip: str, hb_interval: float, width: int, height: int):
        self.ip, self.hb_interval = ip, hb_interval
        self.assembler = FrameAssembler(width, height)
        self.sock: Optional[socket.socket] = None
        self.packets: Deque[bytes] = deque(maxlen=512)
        self.stats = {"rx": 0, "bytes": 0, "err": 0}
        self.last_rx = 0.0
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def open(self) -> bool:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", LOCAL_PORT))
            s.settimeout(0.3)
        except OSError as e:
            print(f"[WiFi] не удалось открыть сокет: {e}")
            return False
        self.sock = s
        self._stop.clear()
        self.poke()
        for target in (self._rx_loop, self._hb_loop):
            t = threading.Thread(target=target, daemon=True)
            t.start()
            self._threads.append(t)
        return True

    def poke(self) -> None:
        if not self.sock:
            return
        try:
            self.sock.sendto(WAKE_BYTE, (self.ip, SENSOR_PORT))
            self.sock.sendto(build_heartbeat(), (self.ip, SENSOR_PORT))
        except OSError:
            self.stats["err"] += 1

    def _hb_loop(self) -> None:
        while not self._stop.wait(self.hb_interval):
            self.poke()

    def _rx_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data, _ = self.sock.recvfrom(MAX_DATAGRAM + 64)
            except socket.timeout:
                continue
            except OSError:
                break
            self.packets.append(data)
            self.stats["rx"] += 1
            self.stats["bytes"] += len(data)
            self.last_rx = time.time()

    def read(self) -> Optional[np.ndarray]:
        got = False
        for _ in range(64):
            try:
                pkt = self.packets.popleft()
            except IndexError:
                break
            got |= self.assembler.feed(pkt)
        return self.assembler.image() if got else None

    def status(self) -> str:
        if not self.last_rx:
            state = "ожидание пакетов"
        elif time.time() - self.last_rx > 5:
            state = "таймаут"
        else:
            state = "поток идёт"
        return f"wifi {state} rx={self.stats['rx']} err={self.stats['err']}"

    def close(self) -> None:
        self._stop.set()
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        for t in self._threads:
            t.join(timeout=1.0)
        self._threads.clear()


def list_usb(max_index: int = 8) -> None:
    print("USB/UVC устройства:")
    found = False
    for i in range(max_index):
        cap = cv2.VideoCapture(i)
        if cap.isOpened() and cap.read()[0]:
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            print(f"  [{i}] {w}x{h}")
            found = True
        cap.release()
    if not found:
        print("  не найдены")


def pick_source(args) -> Optional[Source]:
    candidates = []
    if args.mode in ("usb", "auto"):
        candidates.append(UsbSource(args.device, args.width, args.height))
    if args.mode in ("wifi", "auto"):
        candidates.append(WifiSource(args.ip, args.hb, args.width, args.height))
    for src in candidates:
        print(f"[{src.name}] открываю...")
        if src.open():
            print(f"[{src.name}] OK")
            return src
        print(f"[{src.name}] недоступно")
    return None


# --------------------------------------------------------------------------
# Запись и UI
# --------------------------------------------------------------------------
class Recorder:
    def __init__(self, out_dir: str):
        self.out_dir = out_dir
        self.writer: Optional[cv2.VideoWriter] = None
        self.path = ""

    @property
    def active(self) -> bool:
        return self.writer is not None

    def start(self, frame: np.ndarray, fps: float) -> None:
        h, w = frame.shape[:2]
        self.path = os.path.join(
            self.out_dir, f"usg_{datetime.now():%Y%m%d_%H%M%S}.avi")
        self.writer = cv2.VideoWriter(
            self.path, cv2.VideoWriter_fourcc(*"MJPG"), max(fps, 5.0), (w, h))
        if not self.writer.isOpened():
            self.writer = None
            print("Не удалось начать запись")
        else:
            print(f"Запись: {self.path}")

    def write(self, frame: np.ndarray) -> None:
        if self.writer is not None:
            self.writer.write(frame)

    def stop(self) -> None:
        if self.writer is not None:
            self.writer.release()
            self.writer = None
            print(f"Запись сохранена: {self.path}")


def run(args) -> int:
    os.makedirs(args.output, exist_ok=True)
    src = pick_source(args)
    if src is None:
        print("Нет активного источника. Проверьте USB или Wi-Fi точку датчика.")
        return 1

    window = "Chison USG"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window, 800, 600)

    rec = Recorder(args.output)
    last = np.zeros((args.height, args.width), dtype=np.uint8)
    fps, n, t0 = 0.0, 0, time.time()
    print("Клавиши: s=снимок  r=запись  h=heartbeat  q=выход")

    try:
        with src:
            while True:
                frame = src.read()
                if frame is not None:
                    last = frame
                    n += 1
                else:
                    time.sleep(0.005)

                now = time.time()
                if now - t0 >= 1.0:
                    fps, n, t0 = n / (now - t0), 0, now

                view = cv2.cvtColor(last, cv2.COLOR_GRAY2BGR)
                cv2.putText(view, f"FPS {fps:4.1f}  {src.status()}", (10, 22),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1, cv2.LINE_AA)
                if rec.active:
                    cv2.putText(view, "REC", (10, 44), cv2.FONT_HERSHEY_SIMPLEX,
                                0.55, (0, 0, 255), 1, cv2.LINE_AA)
                    if frame is not None:
                        rec.write(view)
                cv2.imshow(window, view)

                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
                if key == ord("s"):
                    path = os.path.join(
                        args.output, f"usg_{datetime.now():%Y%m%d_%H%M%S_%f}.png")
                    cv2.imwrite(path, last)
                    print(f"Снимок: {path}")
                elif key == ord("r"):
                    rec.stop() if rec.active else rec.start(view, fps or 15.0)
                elif key == ord("h"):
                    src.poke()
                if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                    break
    except KeyboardInterrupt:
        pass
    finally:
        rec.stop()
        cv2.destroyAllWindows()
        print("Завершено.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Chison USG — просмотр с УЗИ-датчика")
    p.add_argument("-m", "--mode", choices=("auto", "usb", "wifi"), default="auto")
    p.add_argument("--ip", default=SENSOR_IP, help="IP датчика (Wi-Fi)")
    p.add_argument("-d", "--device", type=int, default=0, help="индекс USB-камеры")
    p.add_argument("--width", type=int, default=512)
    p.add_argument("--height", type=int, default=512)
    p.add_argument("-o", "--output", default="snapshots", help="папка снимков/видео")
    p.add_argument("--hb", type=float, default=2.0, help="интервал heartbeat, сек")
    p.add_argument("--list-usb", action="store_true", help="список USB-камер")
    args = p.parse_args()
    if args.list_usb:
        list_usb()
        return 0
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
