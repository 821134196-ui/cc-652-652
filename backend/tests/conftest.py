"""Pytest setup: isolated temp data dir, fresh DB, in-process FastAPI client."""
from __future__ import annotations

import os
import tempfile

import pytest

# Must point at an empty temp dir BEFORE the app package is imported, since the
# engine/data paths are created at import time.
_TMP = tempfile.mkdtemp(prefix="consent-test-")
os.environ.setdefault("APP_DATA_DIR", _TMP)

from fastapi.testclient import TestClient  # noqa: E402

from app.database import CONSENT_FILE_DIR, init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    init_db()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def patient_factory(client):
    counter = {"n": 0}

    def _make(name: str = "张三", authorized_family: bool | None = None):
        counter["n"] += 1
        mrn = f"MRN-TEST-{counter['n']}-{name}"
        r = client.post("/api/patients", json={"name": name, "mrn": mrn})
        assert r.status_code == 200, r.text
        patient = r.json()

        family = None
        if authorized_family is not None:
            r = client.post(
                f"/api/patients/{patient['id']}/family",
                json={
                    "name": f"{name}家属",
                    "relationship": "配偶",
                    "authorized": authorized_family,
                    "authorization_note": "授权委托书已核验" if authorized_family else None,
                },
            )
            assert r.status_code == 200, r.text
            family = r.json()
        return patient, family

    return _make


@pytest.fixture
def surgery_factory(client):
    def _make(patient_id: int, name: str = "腹腔镜胆囊切除术"):
        r = client.post(
            "/api/surgeries", json={"patient_id": patient_id, "name": name}
        )
        assert r.status_code == 200, r.text
        return r.json()

    return _make


def make_version(client, surgery_id, *, procedure="腹腔镜胆囊切除术", anesthesia="全身麻醉",
                 risks="出血、胆漏、周围脏器损伤", change_summary=None):
    payload = {
        "procedure": procedure,
        "anesthesia": anesthesia,
        "risks": risks,
    }
    if change_summary is not None:
        payload["change_summary"] = change_summary
    r = client.post(f"/api/surgeries/{surgery_id}/versions", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def version_factory(client):
    def _make(surgery_id, **kwargs):
        return make_version(client, surgery_id, **kwargs)

    return _make
