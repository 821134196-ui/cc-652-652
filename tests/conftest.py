"""测试基础设施：隔离的数据目录 + TestClient + 真实 uvicorn 服务（并发用）。"""
import os
import tempfile

# 必须在导入 app 之前设置，保证测试使用独立 SQLite 与文件目录
os.environ["APP_DATA_DIR"] = tempfile.mkdtemp(prefix="consent-test-")

import socket  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
import uvicorn  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

DATA_DIR = os.environ["APP_DATA_DIR"]
FILES_DIR = os.path.join(DATA_DIR, "files")


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def live_server():
    """真实 uvicorn 服务（独立线程），用于真正的并发请求测试。"""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "测试服务启动失败"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(timeout=10)


# ---------------------------------------------------------------- 辅助函数

def make_patient(client, name=None, mrn=None):
    name = name or f"患者{uuid.uuid4().hex[:6]}"
    mrn = mrn or f"T{uuid.uuid4().hex[:8].upper()}"
    r = client.post("/api/patients", json={"name": name, "mrn": mrn})
    assert r.status_code == 201, r.text
    return r.json()


def add_family(client, patient_id, name, relationship="子女"):
    r = client.post(f"/api/patients/{patient_id}/authorized-family",
                    json={"name": name, "relationship": relationship})
    assert r.status_code == 201, r.text
    return r.json()


def make_surgery(client, patient_id, title=None, surgeon="测试医生"):
    title = title or f"测试手术{uuid.uuid4().hex[:6]}"
    r = client.post("/api/surgeries", json={
        "patient_id": patient_id, "title": title,
        "surgeon": surgeon, "scheduled_at": "2026-10-08T09:00:00",
    })
    assert r.status_code == 201, r.text
    return r.json()


def make_version(client, surgery_id, risks="风险A", procedure="术式A", anesthesia="全麻"):
    r = client.post(f"/api/surgeries/{surgery_id}/consent-versions", json={
        "procedure": procedure, "anesthesia": anesthesia,
        "risks": risks, "created_by": "测试医生",
    })
    assert r.status_code == 201, r.text
    return r.json()


def get_receipt(client, signer_name, document_hash):
    r = client.post("/api/esign/receipts", json={
        "signer_name": signer_name, "document_hash": document_hash,
    })
    assert r.status_code == 201, r.text
    return r.json()


def sign(client, version_id, signer_name, role, content_hash,
         relationship=None, receipt=None):
    """完整签署流程：取回执 -> 提交。receipt 可传入以复用/伪造。"""
    receipt = receipt or get_receipt(client, signer_name, content_hash)
    return client.post(f"/api/consent-versions/{version_id}/sign", json={
        "signer_name": signer_name,
        "signer_role": role,
        "relationship": relationship,
        "signature_text": receipt["signature_text"],
        "receipt_id": receipt["receipt_id"],
    })


def detail(client, surgery_id):
    r = client.get(f"/api/surgeries/{surgery_id}")
    assert r.status_code == 200, r.text
    return r.json()


def readiness(client, surgery_id):
    r = client.get(f"/api/surgeries/{surgery_id}/readiness")
    assert r.status_code == 200, r.text
    return r.json()


def notifications(client, surgery_id):
    r = client.get(f"/api/notifications", params={"surgery_id": surgery_id})
    assert r.status_code == 200, r.text
    return r.json()


# 供 live_server 场景使用的 httpx 版本
class Http:
    def __init__(self, base):
        self.base = base

    def post(self, path, json=None):
        return httpx.post(self.base + path, json=json, timeout=15)

    def get(self, path, params=None):
        return httpx.get(self.base + path, params=params, timeout=15)
