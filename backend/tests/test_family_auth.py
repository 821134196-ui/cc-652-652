"""流程四：家属授权校验——未授权家属不能完成有效签署。"""
from __future__ import annotations


def test_unauthorized_family_cannot_sign(client, patient_factory, surgery_factory, version_factory):
    patient, unauthorized = patient_factory(name="王五", authorized_family=False)
    surgery = surgery_factory(patient["id"])
    sid = surgery["id"]
    version_factory(sid)

    r = client.post(
        f"/api/surgeries/{sid}/sign",
        json={"signer_type": "FAMILY", "family_id": unauthorized["id"]},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "UNAUTHORIZED_FAMILY"

    # 拒签后没有任何签名/文件产生，排期仍为待签署
    detail = client.get(f"/api/surgeries/{sid}").json()
    assert detail["versions"][0]["signature"] is None
    assert detail["readiness"]["readiness"] == "UNSIGNED"

    # 未授权家属也不能撤回
    r = client.post(
        f"/api/surgeries/{sid}/withdraw",
        json={"signer_type": "FAMILY", "family_id": unauthorized["id"], "reason": "x"},
    )
    assert r.status_code == 403

    # 补齐授权后可以有效签署
    r = client.patch(
        f"/api/family/{unauthorized['id']}/authorization",
        json={"authorized": True, "authorization_note": "补交授权委托书"},
    )
    assert r.status_code == 200 and r.json()["authorized"] is True

    r = client.post(
        f"/api/surgeries/{sid}/sign",
        json={"signer_type": "FAMILY", "family_id": unauthorized["id"]},
    )
    assert r.status_code == 200 and r.json()["created"] is True
    assert r.json()["signature"]["signer_type"] == "FAMILY"
    detail = client.get(f"/api/surgeries/{sid}").json()
    assert detail["readiness"]["readiness"] == "READY"


def test_family_of_other_patient_rejected(client, patient_factory, surgery_factory, version_factory):
    p1, _ = patient_factory(name="赵六")
    _, family = patient_factory(name="钱七", authorized_family=True)
    surgery = surgery_factory(p1["id"])
    sid = surgery["id"]
    version_factory(sid)

    r = client.post(
        f"/api/surgeries/{sid}/sign",
        json={"signer_type": "FAMILY", "family_id": family["id"]},
    )
    assert r.status_code == 404
    detail = client.get(f"/api/surgeries/{sid}").json()
    assert detail["versions"][0]["signature"] is None


def test_revoked_authorization_blocks_new_version(client, patient_factory, surgery_factory, version_factory):
    patient, family = patient_factory(name="孙八", authorized_family=True)
    surgery = surgery_factory(patient["id"])
    sid = surgery["id"]
    version_factory(sid)

    assert client.post(
        f"/api/surgeries/{sid}/sign", json={"signer_type": "FAMILY", "family_id": family["id"]}
    ).json()["created"] is True

    # 方案变更后撤销家属授权：该家属对新版的签署无效
    version_factory(sid, procedure="变更术式", change_summary="术式调整")
    client.patch(f"/api/family/{family['id']}/authorization", json={"authorized": False})

    r = client.post(
        f"/api/surgeries/{sid}/sign", json={"signer_type": "FAMILY", "family_id": family["id"]}
    )
    assert r.status_code == 403
    detail = client.get(f"/api/surgeries/{sid}").json()
    assert detail["readiness"]["readiness"] == "RESIGN_REQUIRED"

    # 患者本人签署不受影响
    r = client.post(f"/api/surgeries/{sid}/sign", json={"signer_type": "PATIENT"})
    assert r.status_code == 200 and r.json()["created"] is True
    assert client.get(f"/api/surgeries/{sid}").json()["readiness"]["readiness"] == "READY"
