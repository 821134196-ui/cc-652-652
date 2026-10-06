"""四大核心流程验证：方案变更、撤回、并发签署、家属权限。"""
import os
from concurrent.futures import ThreadPoolExecutor

from conftest import (
    FILES_DIR,
    Http,
    add_family,
    detail,
    get_receipt,
    make_patient,
    make_surgery,
    make_version,
    notifications,
    readiness,
    sign,
)


# ---------------------------------------------------------------- 方案变更

def test_plan_change_invalidates_old_signature(client):
    patient = make_patient(client)
    surgery = make_surgery(client, patient["id"])
    sid = surgery["id"]

    # 未出具版本：不可安排
    r = readiness(client, sid)
    assert r["ready"] is False and r["state"] == "NO_VERSION"

    v1 = make_version(client, sid, risks="风险：出血、感染")
    assert readiness(client, sid)["state"] == "PENDING_SIGNATURE"

    # 关键内容未变化时不允许产生新版本
    dup = client.post(f"/api/surgeries/{sid}/consent-versions", json={
        "procedure": "术式A", "anesthesia": "全麻",
        "risks": "风险：出血、感染", "created_by": "测试医生",
    })
    assert dup.status_code == 409

    # 患者签署 v1 -> 可安排
    resp = sign(client, v1["id"], patient["name"], "PATIENT", v1["content_hash"])
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "VALID"
    assert readiness(client, sid)["ready"] is True

    # 方案变更：出具 v2（风险内容变化）
    v2 = make_version(client, sid, risks="风险：出血、感染、邻近器官损伤")
    assert v2["version_no"] == 2

    d = detail(client, sid)
    v1_after = next(v for v in d["versions"] if v["version_no"] == 1)
    assert v1_after["status"] == "SUPERSEDED"
    assert v1_after["signatures"][0]["status"] == "INVALID"
    assert "方案变更" in v1_after["signatures"][0]["invalidate_reason"]

    # 排期页：不再满足手术准备条件，提示重新签署
    r = readiness(client, sid)
    assert r["ready"] is False and r["state"] == "PENDING_SIGNATURE"
    assert r["current_version_no"] == 2

    # 旧版本不能补签（旧签名不可复制/沿用）
    old_sign = sign(client, v1["id"], patient["name"], "PATIENT", v1["content_hash"])
    assert old_sign.status_code == 409

    # 旧回执（绑定 v1 内容哈希）不能用于新版本
    stale_receipt = get_receipt(client, patient["name"], v1["content_hash"])
    resp = client.post(f"/api/consent-versions/{v2['id']}/sign", json={
        "signer_name": patient["name"], "signer_role": "PATIENT",
        "relationship": None, "signature_text": stale_receipt["signature_text"],
        "receipt_id": stale_receipt["receipt_id"],
    })
    assert resp.status_code == 400
    assert "旧版本" in resp.json()["detail"]

    # 新回执签署 v2 -> 恢复可安排
    resp = sign(client, v2["id"], patient["name"], "PATIENT", v2["content_hash"])
    assert resp.status_code == 201
    assert readiness(client, sid)["ready"] is True

    # 不变量：同一手术只有一份当前有效版本
    actives = [v for v in detail(client, sid)["versions"] if v["status"] == "ACTIVE"]
    assert len(actives) == 1 and actives[0]["version_no"] == 2

    # 历史文件保留在本地
    assert os.path.exists(os.path.join(FILES_DIR, v1_after["file_path"]))
    notes = notifications(client, sid)
    assert any(n["type"] == "VERSION_SUPERSEDED" for n in notes)


# ---------------------------------------------------------------- 撤回

def test_withdrawal_flow(client):
    patient = make_patient(client)
    surgery = make_surgery(client, patient["id"], surgeon="王医生")
    sid = surgery["id"]
    v1 = make_version(client, sid)
    sign(client, v1["id"], patient["name"], "PATIENT", v1["content_hash"])
    assert readiness(client, sid)["ready"] is True

    # 非患者本人不能撤回
    r = client.post(f"/api/consent-versions/{v1['id']}/withdraw",
                    json={"withdrawn_by": "别人", "reason": "test"})
    assert r.status_code == 403

    # 患者撤回
    r = client.post(f"/api/consent-versions/{v1['id']}/withdraw",
                    json={"withdrawn_by": patient["name"], "reason": "想再考虑一下"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "WITHDRAWN"
    assert body["withdrawn_by"] == patient["name"]

    # 原文件失效：版本与签名均失效，排期不满足
    d = detail(client, sid)
    v1_after = d["versions"][0]
    assert v1_after["status"] == "WITHDRAWN"
    assert v1_after["signatures"][0]["status"] == "INVALID"
    r = readiness(client, sid)
    assert r["ready"] is False and r["state"] == "WITHDRAWN"

    # 已通知手术团队（主刀医生 + 排期）
    notes = [n for n in notifications(client, sid) if n["type"] == "CONSENT_WITHDRAWN"]
    assert notes, "应有撤回通知"
    assert "王医生" in notes[0]["recipients"] and "scheduler" in notes[0]["recipients"]

    # 历史文件保留
    assert os.path.exists(os.path.join(FILES_DIR, v1_after["file_path"]))
    content = open(os.path.join(FILES_DIR, v1_after["file_path"]), encoding="utf-8").read()
    assert "患者已撤回" in content

    # 重复撤回 / 已撤回版本再签署均拒绝
    assert client.post(f"/api/consent-versions/{v1['id']}/withdraw",
                       json={"withdrawn_by": patient["name"]}).status_code == 409
    assert sign(client, v1["id"], patient["name"], "PATIENT",
                v1["content_hash"]).status_code == 409

    # 撤回后可重新出具并签署，恢复可安排
    v2 = make_version(client, sid, risks="风险B")
    assert sign(client, v2["id"], patient["name"], "PATIENT",
                v2["content_hash"]).status_code == 201
    assert readiness(client, sid)["ready"] is True


def test_withdraw_unsigned_version_rejected(client):
    patient = make_patient(client)
    surgery = make_surgery(client, patient["id"])
    v1 = make_version(client, surgery["id"])
    r = client.post(f"/api/consent-versions/{v1['id']}/withdraw",
                    json={"withdrawn_by": patient["name"]})
    assert r.status_code == 409  # 未签署无需撤回


# ---------------------------------------------------------------- 并发签署

def test_concurrent_signing_only_one_valid(live_server):
    http = Http(live_server)
    patient = make_patient(http)
    surgery = make_surgery(http, patient["id"])
    v1 = make_version(http, surgery["id"])
    add_family(http, patient["id"], "授权家属甲", "配偶")

    # 8 个签署人（患者 + 授权家属 + 若干未授权者）同时签署同一版本
    signers = [(patient["name"], "PATIENT", None), ("授权家属甲", "FAMILY", "配偶")]
    signers += [(f"家属{i}", "FAMILY", "亲属") for i in range(6)]

    def attempt(s):
        name, role, rel = s
        receipt = get_receipt(http, name, v1["content_hash"])
        return http.post(f"/api/consent-versions/{v1['id']}/sign", json={
            "signer_name": name, "signer_role": role, "relationship": rel,
            "signature_text": receipt["signature_text"],
            "receipt_id": receipt["receipt_id"],
        })

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, signers))

    codes = sorted(r.status_code for r in results)
    # 未授权者 403；授权者中恰好 1 个 201，其余 409
    assert codes.count(403) == 6
    assert codes.count(201) == 1
    assert codes.count(409) == 1

    d = detail(http, surgery["id"])
    valid = [s for s in d["versions"][0]["signatures"] if s["status"] == "VALID"]
    assert len(valid) == 1, "并发签署绝不能产生两份有效文件"
    assert readiness(http, surgery["id"])["ready"] is True


def test_duplicate_receipt_is_idempotent(live_server):
    http = Http(live_server)
    patient = make_patient(http)
    surgery = make_surgery(http, patient["id"])
    v1 = make_version(http, surgery["id"])

    receipt = get_receipt(http, patient["name"], v1["content_hash"])
    payload = {
        "signer_name": patient["name"], "signer_role": "PATIENT",
        "relationship": None, "signature_text": receipt["signature_text"],
        "receipt_id": receipt["receipt_id"],
    }

    # 同一回执并发重复送达 6 次
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(
            lambda _: http.post(f"/api/consent-versions/{v1['id']}/sign", json=payload),
            range(6),
        ))
    assert all(r.status_code == 201 for r in results), [r.status_code for r in results]
    sig_ids = {r.json()["id"] for r in results}
    assert len(sig_ids) == 1, "重复回执只能对应同一条签名记录"
    assert any(r.json()["idempotent_replay"] for r in results)

    # 顺序再送达一次：仍幂等
    again = http.post(f"/api/consent-versions/{v1['id']}/sign", json=payload)
    assert again.status_code == 201 and again.json()["idempotent_replay"] is True

    d = detail(http, surgery["id"])
    valid = [s for s in d["versions"][0]["signatures"] if s["status"] == "VALID"]
    assert len(valid) == 1


def test_concurrent_version_creation_keeps_single_active(live_server):
    http = Http(live_server)
    patient = make_patient(http)
    surgery = make_surgery(http, patient["id"])

    def create(i):
        return http.post(f"/api/surgeries/{surgery['id']}/consent-versions", json={
            "procedure": f"术式{i}", "anesthesia": "全麻",
            "risks": f"风险{i}", "created_by": "测试医生",
        })

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(create, range(5)))

    codes = sorted(r.status_code for r in results)
    assert codes.count(201) == 1 and codes.count(409) == 4
    actives = [v for v in detail(http, surgery["id"])["versions"] if v["status"] == "ACTIVE"]
    assert len(actives) == 1, "同一手术只能有一份当前有效版本"


# ---------------------------------------------------------------- 家属权限

def test_family_authorization(client):
    patient = make_patient(client)
    surgery = make_surgery(client, patient["id"])
    sid = surgery["id"]
    v1 = make_version(client, sid)
    h = v1["content_hash"]

    # 未获授权的家属 -> 403，且留下拒签告警通知
    r = sign(client, v1["id"], "陌生人", "FAMILY", h, relationship="朋友")
    assert r.status_code == 403
    assert "未获患者授权" in r.json()["detail"]
    notes = [n for n in notifications(client, sid) if n["type"] == "SIGN_REJECTED"]
    assert notes and "陌生人" in notes[0]["message"]

    # 冒用患者名义 -> 403
    r = sign(client, v1["id"], "陌生人", "PATIENT", h)
    assert r.status_code == 403

    # 授权家属但缺关系 -> 422
    add_family(client, patient["id"], "张莉", "女儿")
    r = sign(client, v1["id"], "张莉", "FAMILY", h)
    assert r.status_code == 422

    # 授权家属完整信息 -> 201
    r = sign(client, v1["id"], "张莉", "FAMILY", h, relationship="女儿")
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "VALID"
    assert readiness(client, sid)["ready"] is True

    # 已签版本不能产生第二份有效签署（患者本人也不行）
    r = sign(client, v1["id"], patient["name"], "PATIENT", h)
    assert r.status_code == 409
    d = detail(client, sid)
    valid = [s for s in d["versions"][0]["signatures"] if s["status"] == "VALID"]
    assert len(valid) == 1 and valid[0]["signer_name"] == "张莉"
