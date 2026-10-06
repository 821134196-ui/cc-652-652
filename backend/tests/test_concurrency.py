"""流程三：多人并发签字 / 签名回执重复投递，只能产生一份有效文件。

这些用例启动一个真实的 uvicorn 服务（后台线程），用线程池并发打 HTTP，
以覆盖 TestClient 串行调用无法触发的数据库写竞争。
"""
from __future__ import annotations

import socket
import threading
import time

import httpx
import pytest
import uvicorn

from app.database import CONSENT_FILE_DIR
from app.main import app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server():
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            if httpx.get(f"{base}/api/health", timeout=1).status_code == 200:
                break
        except httpx.TransportError:
            time.sleep(0.05)
    else:
        raise RuntimeError("test server did not start")
    yield base
    server.should_exit = True
    thread.join(timeout=5)


def _setup_surgery(base: str, authorized_family: bool = True) -> tuple[int, int | None]:
    r = httpx.post(f"{base}/api/patients", json={"name": "并发测试患者", "mrn": f"MRN-C-{time.time_ns()}"})
    pid = r.json()["id"]
    fid = None
    if authorized_family:
        r = httpx.post(
            f"{base}/api/patients/{pid}/family",
            json={"name": "并发家属", "relationship": "子女", "authorized": True,
                  "authorization_note": "已授权"},
        )
        fid = r.json()["id"]
    r = httpx.post(f"{base}/api/surgeries", json={"patient_id": pid, "name": "并发签字手术"})
    sid = r.json()["id"]
    r = httpx.post(
        f"{base}/api/surgeries/{sid}/versions",
        json={"procedure": "测试术式", "anesthesia": "全麻", "risks": "测试风险"},
    )
    assert r.status_code == 200
    return sid, fid


def _assert_single_effective_signature(base: str, sid: int):
    detail = httpx.get(f"{base}/api/surgeries/{sid}").json()
    active = [v for v in detail["versions"] if v["status"] == "ACTIVE"]
    assert len(active) == 1
    assert active[0]["signature"] is not None
    signed = [v for v in detail["versions"] if v["signature"] is not None]
    assert len(signed) == 1
    files = list((CONSENT_FILE_DIR / f"surgery_{sid}").glob("v*_signed_*"))
    assert len(files) == 1, f"期望 1 份签署文件，实际 {len(files)}"
    return active[0]["signature"]["gateway_receipt_id"]


def test_concurrent_patient_and_family_sign(live_server):
    base = live_server
    sid, fid = _setup_surgery(base)

    # 6 个并发请求：患者、授权家属、不同客户端同时提交，含重复的幂等键
    payloads = [
        {"signer_type": "PATIENT"},
        {"signer_type": "FAMILY", "family_id": fid},
        {"signer_type": "PATIENT", "idempotency_key": "client-key-001"},
        {"signer_type": "FAMILY", "family_id": fid, "idempotency_key": "client-key-002"},
        {"signer_type": "PATIENT"},
        {"signer_type": "PATIENT", "idempotency_key": "client-key-001"},
    ]
    with httpx.Client(timeout=30) as http:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=6) as pool:
            responses = list(pool.map(
                lambda p: http.post(f"{base}/api/surgeries/{sid}/sign", json=p), payloads
            ))

    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    created_flags = [r.json()["created"] for r in responses]
    assert created_flags.count(True) == 1, f"应只有一次创建，实际 {created_flags}"
    receipts = {r.json()["signature"]["gateway_receipt_id"] for r in responses}
    assert receipts == {_assert_single_effective_signature(base, sid)}


def test_duplicate_gateway_receipt_redelivery(live_server):
    base = live_server
    sid, fid = _setup_surgery(base)
    receipt = "RCP-REDELIVERY-0001"

    # 模拟签名网关把同一份回执重复推送 4 次（并发）
    payloads = [
        {"signer_type": "PATIENT", "gateway_receipt_id": receipt},
        {"signer_type": "PATIENT", "gateway_receipt_id": receipt},
        {"signer_type": "FAMILY", "family_id": fid, "gateway_receipt_id": receipt},
        {"signer_type": "PATIENT", "gateway_receipt_id": receipt},
    ]
    with httpx.Client(timeout=30) as http:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(
                lambda p: http.post(f"{base}/api/surgeries/{sid}/sign", json=p), payloads
            ))

    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    assert [r.json()["created"] for r in responses].count(True) == 1
    assert _assert_single_effective_signature(base, sid) == receipt


def test_concurrent_sign_after_plan_change(live_server):
    base = live_server
    sid, fid = _setup_surgery(base)

    with httpx.Client(timeout=30) as http:
        # v1 先由家属签
        r = http.post(f"{base}/api/surgeries/{sid}/sign",
                      json={"signer_type": "FAMILY", "family_id": fid})
        assert r.json()["created"] is True
        # 方案变更
        r = http.post(
            f"{base}/api/surgeries/{sid}/versions",
            json={"procedure": "变更后术式", "anesthesia": "局麻",
                  "risks": "变更后风险", "change_summary": "麻醉方式变更"},
        )
        assert r.status_code == 200

        payloads = [{"signer_type": "PATIENT"}] * 5
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=5) as pool:
            responses = list(pool.map(
                lambda p: http.post(f"{base}/api/surgeries/{sid}/sign", json=p), payloads
            ))

    assert [r.json()["created"] for r in responses].count(True) == 1
    detail = httpx.get(f"{base}/api/surgeries/{sid}").json()
    assert detail["readiness"]["readiness"] == "READY"
    v2 = next(v for v in detail["versions"] if v["version_no"] == 2)
    assert v2["signature"] is not None
    # v1 的旧文件与 v2 的新文件各一份
    d = CONSENT_FILE_DIR / f"surgery_{sid}"
    assert len(list(d.glob("v1_signed_*"))) == 1
    assert len(list(d.glob("v2_signed_*"))) == 1
