# -*- coding: utf-8 -*-
"""
Chison UDP bridge.
Раздаёт index.html + API для браузера. Общается с датчиком по UDP.
Запуск: python bridge.py    →   http://127.0.0.1:8765
"""
from __future__ import annotations

import os
import socket
import time
from typing import Any, Dict

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from protocol import (
    build_freetest_block,
    build_heartbeat,
    SENSOR_IP,
    SENSOR_PORT,
    LOCAL_PORT,
)
from license_bypass import decode_key, set_probe_valid, maintenance_ok

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "index.html")

app = FastAPI(title="Chison UDP bridge", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# UDP-сокет: шлём на датчик, слушаем тот же порт (best-effort)
_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
_sock.settimeout(0.0)
try:
    _sock.bind(("0.0.0.0", LOCAL_PORT))
except OSError:
    pass  # порт занят — просто отправляем, слушать не будем


def _send(payload: bytes, host: str, port: int) -> Dict[str, Any]:
    try:
        n = _sock.sendto(payload, (host, int(port)))
        return {"ok": True, "sent": n, "host": host, "port": int(port)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "host": host, "port": int(port)}


# ---------- Статика: index.html ----------
@app.get("/")
def root():
    if os.path.isfile(INDEX):
        return FileResponse(INDEX, media_type="text/html; charset=utf-8")
    return JSONResponse({"error": "index.html не найден рядом с bridge.py"}, status_code=404)


# ---------- Health ----------
@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "chison-bridge",
        "ts": int(time.time()),
        "sensor_default": f"{SENSOR_IP}:{SENSOR_PORT}",
    }


# ---------- Validate: HTTP → UDP ----------
class ValidateReq(BaseModel):
    probe: str = "ANY"
    key: str = ""
    rent: bool = False
    host: str = SENSOR_IP
    port: int = SENSOR_PORT


@app.get("/validate")
def validate(
    probe: str = Query("ANY"),
    key: str = Query(""),
    rent: bool = Query(False),
    host: str = Query(SENSOR_IP),
    port: int = Query(SENSOR_PORT),
):
    lic = decode_key(key, probe, rent).to_dict()
    pi = set_probe_valid(probe, probe)
    block = build_freetest_block(name=probe, tenancy=-1)
    udp = _send(block, host, port)
    return {
        **lic,
        "_pi": pi["pi"],
        "_udp": udp,
        "_block_len": len(block),
        "_bridge": True,
    }


@app.post("/validate")
def validate_post(req: ValidateReq):
    return validate(probe=req.probe, key=req.key, rent=req.rent, host=req.host, port=req.port)


# ---------- Heartbeat ----------
@app.get("/heartbeat")
def heartbeat(host: str = Query(SENSOR_IP), port: int = Query(SENSOR_PORT)):
    hb = build_heartbeat()
    udp = _send(hb, host, port)
    return {"ok": udp["ok"], "len": len(hb), "_udp": udp}


# ---------- Maintenance ----------
@app.get("/maintenance")
def maintenance(role: str = Query("Service")):
    return maintenance_ok(role)


# ---------- Диагностика сокета ----------
@app.get("/diag")
def diag():
    try:
        me = _sock.getsockname()
    except Exception as e:
        me = f"error: {e}"
    return {
        "local_socket": me,
        "target": f"{SENSOR_IP}:{SENSOR_PORT}",
        "index_present": os.path.isfile(INDEX),
    }


if __name__ == "__main__":
    import uvicorn
    print("▶  Chison bridge на http://127.0.0.1:8765  (Ctrl+C — стоп)")
    print(f"   Раздаёт: {INDEX}")
    print(f"   Целится в датчик: {SENSOR_IP}:{SENSOR_PORT}")
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")