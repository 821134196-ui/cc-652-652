"""Seed a small demo dataset so the UI is immediately explorable after launch.

Covers every readiness state: RESIGN_REQUIRED / READY / UNSIGNED / WITHDRAWN,
plus an unauthorized family member for demonstrating rejection.
Safe to run repeatedly: skips seeding when patients already exist.
"""
from __future__ import annotations

from sqlalchemy import select

from app.database import SessionLocal, init_db
from app.models import FamilyMember, Patient, Surgery
from app import services
from app.schemas import SignRequest, VersionCreate, WithdrawRequest


def _family(db, patient_id, name, relation, authorized, note=None):
    f = FamilyMember(
        patient_id=patient_id, name=name, relation=relation,
        authorized=authorized, authorization_note=note,
    )
    db.add(f)
    db.commit()
    db.refresh(f)
    return f


def _surgery(db, patient_id, name):
    row = Surgery(patient_id=patient_id, name=name)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def seed(db) -> None:
    init_db()
    if db.scalar(select(Patient.id).limit(1)) is not None:
        print("seed: 数据已存在，跳过。")
        return

    # 1) 方案已变更、等待重签 —— 排期页核心场景
    p1 = Patient(name="王秀兰", mrn="MRN-2026-0001")
    db.add(p1); db.commit(); db.refresh(p1)
    _family(db, p1.id, "王建军", "儿子", True, "授权委托书已核验")
    row = _surgery(db, p1.id, "腹腔镜胆囊切除术")
    services.create_version(db, row.id, VersionCreate(
        procedure="腹腔镜胆囊切除术", anesthesia="全身麻醉",
        risks="出血、胆漏、周围脏器损伤、麻醉相关风险"))
    services.sign_active_version(db, row.id, SignRequest(signer_type="PATIENT"))
    services.create_version(db, row.id, VersionCreate(
        procedure="开腹胆囊切除术",
        anesthesia="全身麻醉联合硬膜外镇痛",
        risks="出血、胆漏、感染、开腹相关并发症、心脑血管意外",
        change_summary="术前复评认为腹腔镜条件不足，转为开腹，麻醉方式同步调整"))

    # 2) 已完成有效签署，可排期；另有一名未授权家属可演示拒签
    p2 = Patient(name="李国强", mrn="MRN-2026-0002")
    db.add(p2); db.commit(); db.refresh(p2)
    _family(db, p2.id, "李敏", "女儿", False, "授权材料不齐")
    row2 = _surgery(db, p2.id, "右侧腹股沟疝无张力修补术")
    services.create_version(db, row2.id, VersionCreate(
        procedure="右侧腹股沟疝无张力修补术", anesthesia="椎管内麻醉",
        risks="出血、感染、阴囊血肿、慢性疼痛、补片排异"))
    services.sign_active_version(db, row2.id, SignRequest(signer_type="PATIENT"))

    # 3) 同意书已生成但尚未签署
    p3 = Patient(name="赵敏", mrn="MRN-2026-0003")
    db.add(p3); db.commit(); db.refresh(p3)
    _family(db, p3.id, "赵刚", "配偶", True)
    row3 = _surgery(db, p3.id, "甲状腺左叶次全切除术")
    services.create_version(db, row3.id, VersionCreate(
        procedure="甲状腺左叶次全切除术", anesthesia="全身麻醉",
        risks="出血、喉返神经损伤、低钙抽搐、术后声音改变"))

    # 4) 患者已撤回同意
    p4 = Patient(name="陈建华", mrn="MRN-2026-0004")
    db.add(p4); db.commit(); db.refresh(p4)
    row4 = _surgery(db, p4.id, "右膝关节镜探查清理术")
    services.create_version(db, row4.id, VersionCreate(
        procedure="右膝关节镜探查清理术", anesthesia="全身麻醉",
        risks="出血、感染、血管神经损伤、深静脉血栓"))
    services.sign_active_version(db, row4.id, SignRequest(signer_type="PATIENT"))
    services.withdraw_consent(db, row4.id, WithdrawRequest(
        signer_type="PATIENT", reason="患者决定先尝试保守治疗，暂缓手术"))

    print("seed: 已创建 4 名患者 / 4 台演示手术（须重签、可排期、待签署、已撤回各一）。")


if __name__ == "__main__":
    db = SessionLocal()
    try:
        seed(db)
    finally:
        db.close()
