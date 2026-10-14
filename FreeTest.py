# -*- coding: utf-8 -*-
"""Always-valid license responses for any device/key."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class LicenseResult:
    result: str = "FREETEST_KEY_OK"
    m_function_id: int = 2
    m_probe_id: str = "ANY"
    m_probe_name: str = "ANY"
    m_tenancy: str = "-1"
    m_time_stamp: str = ""
    bypass: bool = True
    note: str = "always-valid: any device accepted"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "result": self.result,
            "mFunctionID": self.m_function_id,
            "mProbeId": self.m_probe_id,
            "mProbeName": self.m_probe_name,
            "mTenancy": self.m_tenancy,
            "mTimeStamp": self.m_time_stamp or str(int(time.time())),
            "_bypass": self.bypass,
            "_note": self.note,
        }


def decode_key(key: str = "", probe_name: str = "ANY", rent: bool = False) -> LicenseResult:
    return LicenseResult(
        m_function_id=1 if rent else 2,
        m_probe_id=probe_name or "ANY",
        m_probe_name=probe_name or "ANY",
    )


def set_probe_valid(probe_id: str = "ANY", name: str = "") -> Dict[str, Any]:
    now = int(time.time())
    return {
        "result": "FREETEST_KEY_OK",
        "pi": {
            "freeTestVersion": "SonoEye P2:1",
            "name": name or probe_id,
            "tenancy": "-1",
            "timeStamp": str(now),
            "regeditTime": str(now),
            "lostTime": "0",
            "currentTime": str(now),
            "ID": "3",
        },
        "_bypass": True,
    }


def maintenance_ok(role: str = "Service") -> Dict[str, Any]:
    return {"ok": True, "name": role, "_bypass": True}
