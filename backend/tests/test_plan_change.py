"""流程一：方案变更后旧签署失效，必须重新签字，不能复制旧签名。"""
from __future__ import annotations

from app.database import CONSENT_FILE_DIR


def test_plan_change_forces_resign(client, patient_factory, surgery_factory, version_factory):
    patient, _ = patient_factory()
    surgery = surgery_factory(patient["id"])
    sid = surgery["id"]

    # 排期页初始：无同意书
    detail = client.get(f"/api/surgeries/{sid}").json()
    assert detail["readiness"]["readiness"] == "NOT_CREATED"

    # 医生生成 v1，患者签署
    v1 = version_factory(sid)
    r = client.post(f"/api/surgeries/{sid}/sign", json={"signer_type": "PATIENT"})
    assert r.status_code == 200
    receipt_v1 = r.json()["signature"]["gateway_receipt_id"]

    detail = client.get(f"/api/surgeries/{sid}").json()
    assert detail["readiness"]["readiness"] == "READY"
    assert len([v for v in detail["versions"] if v["status"] == "ACTIVE"]) == 1

    # 方案关键内容变更：术式从腹腔镜改为开腹
    v2 = version_factory(
        sid,
        procedure="开腹胆囊切除术",
        anesthesia="全身麻醉+硬膜外镇痛",
        risks="出血、胆漏、感染、开腹相关并发症",
        change_summary="术中评估转为开腹，术式与麻醉方式变更",
    )

    detail = client.get(f"/api/surgeries/{sid}").json()
    versions = detail["versions"]
    assert {v["version_no"]: v["status"] for v in versions} == {1: "SUPERSEDED", 2: "ACTIVE"}

    # 同一手术仍然只有一份当前有效同意书
    active = [v for v in versions if v["status"] == "ACTIVE"]
    assert len(active) == 1 and active[0]["version_no"] == 2

    # v1 旧签名被标记失效，但历史文件仍保留在磁盘上
    old_v1 = next(v for v in versions if v["version_no"] == 1)
    assert old_v1["signature"] is not None
    assert old_v1["signature"]["invalidated_at"] is not None
    v1_signed_files = list((CONSENT_FILE_DIR / f"surgery_{sid}").glob("v1_signed_*"))
    assert len(v1_signed_files) == 1

    # 排期页明确提示重新签字
    assert detail["readiness"]["readiness"] == "RESIGN_REQUIRED"
    assert "重新签署" in detail["readiness"]["message"]

    # 试图把旧回执（旧签名）用于新版 → 被视为重放，不会为 v2 产生有效文件
    replay = client.post(
        f"/api/surgeries/{sid}/sign",
        json={"signer_type": "PATIENT", "gateway_receipt_id": receipt_v1},
    )
    # 旧回执只对应 v1，因此 v2 仍未签署（回执查到的是 v1 的旧记录）
    detail = client.get(f"/api/surgeries/{sid}").json()
    v2_row = next(v for v in detail["versions"] if v["version_no"] == 2)
    assert v2_row["signature"] is None
    assert detail["readiness"]["readiness"] == "RESIGN_REQUIRED"
    # 没有产生 v2 的签署文件
    assert list((CONSENT_FILE_DIR / f"surgery_{sid}").glob("v2_signed_*")) == []
    # 重放返回的仍是 v1 的同一条签名记录，没有第二条
    assert replay.status_code == 200
    assert replay.json()["created"] is False
    assert replay.json()["signature"]["gateway_receipt_id"] == receipt_v1

    # 患者对 v2 重新签署 → 具备排期条件；磁盘上 v1/v2 文件各一份
    r = client.post(f"/api/surgeries/{sid}/sign", json={"signer_type": "PATIENT"})
    assert r.status_code == 200 and r.json()["created"] is True
    detail = client.get(f"/api/surgeries/{sid}").json()
    assert detail["readiness"]["readiness"] == "READY"
    assert len(list((CONSENT_FILE_DIR / f"surgery_{sid}").glob("v1_signed_*"))) == 1
    assert len(list((CONSENT_FILE_DIR / f"surgery_{sid}").glob("v2_signed_*"))) == 1

    # 通知手术团队：方案变更需重签
    notes = client.get("/api/notifications", params={"surgery_id": sid}).json()
    assert any(n["type"] == "RESIGN_REQUIRED" and "重新签署" in n["message"] for n in notes)
