"""流程二：患者撤回同意 → 通知团队、文件失效但历史留存、不能排期/签署。"""
from __future__ import annotations

from app.database import CONSENT_FILE_DIR, NOTIFY_GATEWAY_LOG


def test_withdraw_invalidates_and_notifies(client, patient_factory, surgery_factory, version_factory):
    patient, _ = patient_factory(name="李四")
    surgery = surgery_factory(patient["id"], name="甲状腺次全切除术")
    sid = surgery["id"]
    version_factory(sid)

    r = client.post(f"/api/surgeries/{sid}/sign", json={"signer_type": "PATIENT"})
    assert r.status_code == 200
    doc = r.json()["signature"]["document_path"]

    # 撤回
    r = client.post(
        f"/api/surgeries/{sid}/withdraw",
        json={"signer_type": "PATIENT", "reason": "患者决定暂缓手术"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "WITHDRAWN"

    detail = client.get(f"/api/surgeries/{sid}").json()
    assert detail["readiness"]["readiness"] == "WITHDRAWN"
    assert "失效" in detail["readiness"]["message"]

    version = detail["versions"][0]
    assert version["status"] == "WITHDRAWN"
    assert version["signature"]["invalidated_at"] is not None

    # 没有任何 ACTIVE 版本：同一手术不存在第二份"当前有效"同意书
    assert all(v["status"] != "ACTIVE" for v in detail["versions"])

    # 撤回后不能再签署
    r = client.post(f"/api/surgeries/{sid}/sign", json={"signer_type": "PATIENT"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "WITHDRAWN"

    # 通知手术团队
    notes = client.get("/api/notifications", params={"surgery_id": sid}).json()
    assert any(n["type"] == "WITHDRAWN" and "撤回" in n["message"] for n in notes)
    log_text = NOTIFY_GATEWAY_LOG.read_text(encoding="utf-8")
    assert "WITHDRAWN" in log_text

    # 历史文件仍保留在本地，并追加了撤回记录
    from pathlib import Path

    from app.database import BASE_DIR

    path = Path(doc)
    if not path.is_absolute():
        path = BASE_DIR / doc
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "已失效（历史留存）" in content
    assert "撤回记录" in content
    assert "患者决定暂缓手术" in content

    # 医生基于新情况重新生成版本后可重新签署
    v2 = version_factory(sid, change_summary="患者重新考虑后同意，生成新版本")
    r = client.post(f"/api/surgeries/{sid}/sign", json={"signer_type": "PATIENT"})
    assert r.status_code == 200 and r.json()["created"] is True
    detail = client.get(f"/api/surgeries/{sid}").json()
    assert detail["readiness"]["readiness"] == "READY"
    # 磁盘上两份历史文件都在
    files = list((CONSENT_FILE_DIR / f"surgery_{sid}").glob("v*_signed_*"))
    assert len(files) == 2
