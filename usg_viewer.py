#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
USG Viewer — простой просмотрщик изображений с USB-УЗИ датчика
(Cheeson и аналогичных), который подключается по USB/Type-C и определяется
системой как видеоустройство (UVC, то есть "как веб-камера").

Управление в окне:
  s         — сохранить текущий кадр (снимок) в папку snapshots
  r         — начать / остановить запись видео
  q или Esc — выход
"""

import argparse
import os
import sys
import time
from datetime import datetime

try:
    import cv2
except ImportError:
    print("Не найден модуль OpenCV. Установите его командой:")
    print("    pip install opencv-python")
    sys.exit(1)


def list_devices(max_index=10):
    """Перебирает индексы и показывает, какие видеоустройства доступны."""
    print("Поиск доступных видеоустройств...")
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i)
        if cap is not None and cap.isOpened():
            ok, _ = cap.read()
            if ok:
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                print(f"  [{i}] доступно, разрешение {w}x{h}")
                found.append(i)
        cap.release()
    if not found:
        print("  Устройства не найдены. Проверьте подключение датчика по USB.")
    return found


def open_capture(index, width, height):
    cap = cv2.VideoCapture(index)
    if width:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    if height:
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    return cap


def main():
    parser = argparse.ArgumentParser(
        description="Просмотр изображений с USB-УЗИ датчика"
    )
    parser.add_argument("-d", "--device", type=int, default=0,
                        help="индекс видеоустройства (по умолчанию 0)")
    parser.add_argument("--width", type=int, default=0,
                        help="желаемая ширина кадра")
    parser.add_argument("--height", type=int, default=0,
                        help="желаемая высота кадра")
    parser.add_argument("-o", "--output", default="snapshots",
                        help="папка для сохранения снимков и видео")
    parser.add_argument("--list", action="store_true",
                        help="показать список доступных видеоустройств и выйти")
    args = parser.parse_args()

    if args.list:
        list_devices()
        return

    os.makedirs(args.output, exist_ok=True)

    cap = open_capture(args.device, args.width, args.height)
    if not cap.isOpened():
        print(f"Не удалось открыть устройство с индексом {args.device}.")
        print("Запустите с ключом --list, чтобы увидеть доступные устройства.")
        sys.exit(1)

    print("Датчик подключён. Открыто окно с изображением.")
    print("Клавиши: s — снимок, r — запись видео, q/Esc — выход")

    writer = None
    recording = False
    window = "USG Viewer (Cheeson)"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)

    prev = time.time()
    fps = 0.0

    while True:
        ok, frame = cap.read()
        if not ok:
            print("Кадр не получен. Возможно, датчик отключён.")
            break

        now = time.time()
        dt = now - prev
        prev = now
        if dt > 0:
            fps = 0.9 * fps + 0.1 * (1.0 / dt)

        overlay = frame.copy()
        cv2.putText(overlay, f"FPS: {fps:4.1f}", (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        if recording:
            cv2.putText(overlay, "REC", (10, 55),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

        cv2.imshow(window, overlay)

        if recording and writer is not None:
            writer.write(frame)

        key = cv2.waitKey(1) & 0xFF
        if key in (ord('q'), 27):  # q или Esc
            break
        elif key == ord('s'):
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = os.path.join(args.output, f"usg_{ts}.png")
            cv2.imwrite(path, frame)
            print(f"Снимок сохранён: {path}")
        elif key == ord('r'):
            if not recording:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                path = os.path.join(args.output, f"usg_{ts}.avi")
                h, w = frame.shape[:2]
                fourcc = cv2.VideoWriter_fourcc(*"XVID")
                writer = cv2.VideoWriter(path, fourcc, 20.0, (w, h))
                recording = True
                print(f"Запись начата: {path}")
            else:
                recording = False
                if writer is not None:
                    writer.release()
                    writer = None
                print("Запись остановлена.")

        # выход, если окно закрыли крестиком
        if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
            break

    if writer is not None:
        writer.release()
    cap.release()
    cv2.destroyAllWindows()
    print("Завершено.")


if __name__ == "__main__":
    main()
