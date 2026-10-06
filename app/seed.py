"""演示数据：覆盖 已签署有效 / 方案变更待重签 / 待签署 三种排期状态。"""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from .models import AuthorizedFamily, Patient, Surgery
from .schemas import ConsentVersionIn, SignIn, WithdrawIn  # noqa: F401
from .services import create_consent_version, issue_esign_receipt, sign_consent


def seed(db: Session) -> None:
    if db.query(Patient).count() > 0:
        return

    zhang = Patient(mrn="P0001", name="张伟")
    liu = Patient(mrn="P0002", name="刘芳")
    db.add_all([zhang, liu])
    db.flush()
    db.add_all([
        AuthorizedFamily(patient_id=zhang.id, name="张莉", relationship_="女儿"),
        AuthorizedFamily(patient_id=liu.id, name="王强", relationship_="配偶"),
    ])

    tomorrow = datetime.now() + timedelta(days=1)
    day_after = datetime.now() + timedelta(days=2)

    # 手术1：已签署当前版本 -> READY
    s1 = Surgery(patient_id=zhang.id, title="腹腔镜胆囊切除术", surgeon="王医生",
                 scheduled_at=tomorrow.replace(hour=9, minute=0, second=0, microsecond=0))
    db.add(s1)
    db.flush()
    v1 = create_consent_version(db, s1.id, ConsentVersionIn(
        procedure="腹腔镜下胆囊切除，必要时中转开腹",
        anesthesia="全身麻醉",
        risks="出血、胆漏、腹腔感染、麻醉意外",
        created_by="王医生",
    ))
    r1 = issue_esign_receipt(db, zhang.name, v1.content_hash)
    sign_consent(db, v1.id, SignIn(
        signer_name=zhang.name, signer_role="PATIENT",
        signature_text=r1.signature_text, receipt_id=r1.receipt_id,
    ))

    # 手术2：旧版已签，方案变更出新版 -> 需重新签署
    s2 = Surgery(patient_id=liu.id, title="全膝关节置换术（左）", surgeon="李医生",
                 scheduled_at=day_after.replace(hour=10, minute=30, second=0, microsecond=0))
    db.add(s2)
    db.flush()
    v2a = create_consent_version(db, s2.id, ConsentVersionIn(
        procedure="左侧全膝关节置换",
        anesthesia="椎管内麻醉",
        risks="感染、深静脉血栓、假体松动",
        created_by="李医生",
    ))
    r2 = issue_esign_receipt(db, "王强", v2a.content_hash)
    sign_consent(db, v2a.id, SignIn(
        signer_name="王强", signer_role="FAMILY", relationship="配偶",
        signature_text=r2.signature_text, receipt_id=r2.receipt_id,
    ))
    create_consent_version(db, s2.id, ConsentVersionIn(
        procedure="左侧全膝关节置换 + 滑膜切除",
        anesthesia="全身麻醉",
        risks="感染、深静脉血栓、假体松动、全麻相关心肺风险",
        created_by="李医生",
    ))

    # 手术3：已出具版本但尚未签署 -> 待签署
    s3 = Surgery(patient_id=zhang.id, title="白内障超声乳化摘除术（右眼）", surgeon="赵医生",
                 scheduled_at=day_after.replace(hour=14, minute=0, second=0, microsecond=0))
    db.add(s3)
    db.flush()
    create_consent_version(db, s3.id, ConsentVersionIn(
        procedure="右眼白内障超声乳化 + 人工晶体植入",
        anesthesia="表面麻醉",
        risks="后囊膜破裂、眼内炎、角膜水肿",
        created_by="赵医生",
    ))
