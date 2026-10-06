"""核心业务规则。

不变量（应用层校验 + 数据库唯一索引双重保证）：
1. 一台手术同一时刻至多一条 ACTIVE 版本；
2. 一个版本至多一条 VALID 签名；
3. 关键内容变更 => 旧版本及其签名立即失效，绝不允许把旧签名复制到新版；
4. 签名回执全局幂等：重复送达同一回执不会产生第二条签名；
5. 只有患者本人或白名单家属能完成有效签署；只有患者本人能撤回。
"""
import hashlib
import time
import uuid

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from . import storage
from .models import (
    ACTIVE,
    INVALID,
    NOTIFY_CONSENT_SIGNED,
    NOTIFY_CONSENT_WITHDRAWN,
    NOTIFY_SIGN_REJECTED,
    NOTIFY_VERSION_CREATED,
    NOTIFY_VERSION_SUPERSEDED,
    ROLE_FAMILY,
    ROLE_PATIENT,
    SUPERSEDED,
    VALID,
    WITHDRAWN,
    AuthorizedFamily,
    ConsentVersion,
    ESignReceipt,
    Patient,
    Signature,
    Surgery,
    utcnow,
)
from .notifier import notify
from .schemas import ConsentVersionIn, SignIn, WithdrawIn


def content_hash_of(procedure: str, anesthesia: str, risks: str) -> str:
    raw = f"{procedure.strip()}\n{anesthesia.strip()}\n{risks.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_surgery_or_404(db: Session, surgery_id: int) -> Surgery:
    surgery = db.get(Surgery, surgery_id)
    if not surgery:
        raise HTTPException(404, f"手术 {surgery_id} 不存在")
    return surgery


def get_version_or_404(db: Session, version_id: int) -> ConsentVersion:
    version = db.get(ConsentVersion, version_id)
    if not version:
        raise HTTPException(404, f"同意书版本 {version_id} 不存在")
    return version


# ---------------------------------------------------------------- 版本创建

def _is_lock_error(exc: OperationalError) -> bool:
    return "locked" in str(exc).lower()


def create_consent_version(db: Session, surgery_id: int, payload: ConsentVersionIn) -> ConsentVersion:
    try:
        return _create_consent_version_once(db, surgery_id, payload)
    except OperationalError as e:
        # SQLite 写快照冲突：说明有并发建版已提交，按冲突处理让调用方刷新
        db.rollback()
        if _is_lock_error(e):
            raise HTTPException(409, "并发冲突：该手术的同意书正在变更，请刷新后重试")
        raise


def _create_consent_version_once(db: Session, surgery_id: int, payload: ConsentVersionIn) -> ConsentVersion:
    surgery = get_surgery_or_404(db, surgery_id)
    patient = surgery.patient
    new_hash = content_hash_of(payload.procedure, payload.anesthesia, payload.risks)

    current = (
        db.query(ConsentVersion)
        .filter_by(surgery_id=surgery.id, status=ACTIVE)
        .one_or_none()
    )
    if current and current.content_hash == new_hash:
        raise HTTPException(409, "关键内容（术式/麻醉/风险）与当前版本一致，无需新建版本")

    now = utcnow()
    if current:
        # 方案变更：旧版本立即失效，其有效签名同步失效（不可沿用到新版）
        current.status = SUPERSEDED
        current.superseded_at = now
        for sig in current.signatures:
            if sig.status == VALID:
                sig.status = INVALID
                sig.invalidated_at = now
                sig.invalidate_reason = "手术方案变更，版本被取代，需重新签署"
        storage.mark_file_status(current, surgery, patient)
        notify(
            db,
            surgery.id,
            NOTIFY_VERSION_SUPERSEDED,
            f"手术「{surgery.title}」方案变更，V{current.version_no:02d} 已失效，"
            f"原签署作废，患者需重新签署新版本",
            recipients=[surgery.surgeon, "scheduler", patient.name],
        )

    # 直接查库取最大版本号，避免关系缓存导致并发/同事务下取到旧值
    max_no = (
        db.query(func.max(ConsentVersion.version_no))
        .filter(ConsentVersion.surgery_id == surgery.id)
        .scalar()
        or 0
    )
    version = ConsentVersion(
        surgery_id=surgery.id,
        version_no=max_no + 1,
        procedure=payload.procedure,
        anesthesia=payload.anesthesia,
        risks=payload.risks,
        content_hash=new_hash,
        status=ACTIVE,
        created_by=payload.created_by,
        created_at=now,
    )
    db.add(version)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        # 并发建版撞上了唯一索引：另一请求已创建 ACTIVE 版本
        raise HTTPException(409, "并发冲突：该手术已存在当前有效版本，请刷新后重试")

    version.file_path = storage.write_consent_file(version, surgery, patient)
    notify(
        db,
        surgery.id,
        NOTIFY_VERSION_CREATED,
        f"手术「{surgery.title}」知情同意书 V{version.version_no:02d} 已出具，待签署",
        recipients=[patient.name, "scheduler"],
    )
    db.commit()
    db.refresh(version)
    return version


# ---------------------------------------------------------------- 模拟电子签名服务

def issue_esign_receipt(db: Session, signer_name: str, document_hash: str) -> ESignReceipt:
    """本地模拟外部签名服务：为指定签署人 + 文档内容签发回执。"""
    receipt = ESignReceipt(
        receipt_id=uuid.uuid4().hex,
        signer_name=signer_name,
        document_hash=document_hash,
        signature_text=f"ESIGN/{signer_name}/{document_hash[:12]}",
        issued_at=utcnow(),
    )
    db.add(receipt)
    db.commit()
    db.refresh(receipt)
    return receipt


# ---------------------------------------------------------------- 签署

def _check_signer_authorized(db: Session, version: ConsentVersion, payload: SignIn) -> None:
    patient = version.surgery.patient
    if payload.signer_role == ROLE_PATIENT:
        if payload.signer_name != patient.name:
            raise HTTPException(403, f"签署人「{payload.signer_name}」不是患者本人（{patient.name}）")
        return
    # 家属：必须在授权白名单内
    ok = (
        db.query(AuthorizedFamily)
        .filter_by(patient_id=patient.id, name=payload.signer_name)
        .count()
        > 0
    )
    if not ok:
        notify(
            db,
            version.surgery_id,
            NOTIFY_SIGN_REJECTED,
            f"未获授权的家属「{payload.signer_name}」尝试为手术"
            f"「{version.surgery.title}」签署，已拒绝",
            recipients=[version.surgery.surgeon, "scheduler"],
        )
        db.commit()
        raise HTTPException(403, f"家属「{payload.signer_name}」未获患者授权，签署无效")
    if not payload.relationship:
        raise HTTPException(422, "家属签署必须填写与患者的关系")


def sign_consent(db: Session, version_id: int, payload: SignIn) -> tuple[Signature, bool]:
    """返回 (签名, 是否幂等重放)。

    SQLite 并发写冲突（BUSY_SNAPSHOT）时整体重试：重试后能看到胜方已提交
    的状态，从而正确走"回执重放"或"已有有效签署 409"分支。
    """
    for attempt in range(4):
        try:
            return _sign_consent_once(db, version_id, payload)
        except OperationalError as e:
            db.rollback()
            if not _is_lock_error(e) or attempt == 3:
                raise HTTPException(409, "系统繁忙，请刷新后重试")
            time.sleep(0.05 * (attempt + 1))
    raise HTTPException(409, "系统繁忙，请刷新后重试")  # pragma: no cover


def _sign_consent_once(db: Session, version_id: int, payload: SignIn) -> tuple[Signature, bool]:
    version = get_version_or_404(db, version_id)

    # 1) 回执幂等：同一回执重复送达，直接返回首次结果
    existing = db.query(Signature).filter_by(receipt_id=payload.receipt_id).one_or_none()
    if existing:
        if existing.consent_version_id != version_id:
            raise HTTPException(409, "该签名回执已被其他版本使用")
        return existing, True

    # 2) 只有当前有效版本可签：旧版本（被取代/已撤回）一律拒绝，
    #    从入口杜绝"把旧签名复制到新版/旧版补签"
    if version.status != ACTIVE:
        raise HTTPException(
            409,
            f"版本 V{version.version_no:02d} 已失效（{version.status}），不能签署；"
            f"请签署当前有效版本",
        )

    # 3) 校验模拟签名服务回执：签署人一致 + 回执绑定的内容哈希与当前版本一致
    receipt = db.query(ESignReceipt).filter_by(receipt_id=payload.receipt_id).one_or_none()
    if not receipt:
        raise HTTPException(400, "签名回执不存在，请先通过签名服务获取回执")
    if receipt.signer_name != payload.signer_name:
        raise HTTPException(400, "回执签署人与提交人不一致")
    if receipt.document_hash != version.content_hash:
        raise HTTPException(
            400, "签名回执对应的是旧版本内容，不能用于当前版本，请重新获取回执签署"
        )

    # 4) 签署人权限
    _check_signer_authorized(db, version, payload)

    # 5) 已有有效签署？——若正是本回执（并发下对方已提交），按幂等重放返回
    valid = (
        db.query(Signature)
        .filter_by(consent_version_id=version.id, status=VALID)
        .one_or_none()
    )
    if valid:
        if valid.receipt_id == payload.receipt_id:
            return valid, True
        raise HTTPException(409, "该版本已存在有效签署，不能重复签署")

    sig = Signature(
        consent_version_id=version.id,
        signer_name=payload.signer_name,
        signer_role=payload.signer_role,
        relationship_=payload.relationship,
        signature_text=payload.signature_text,
        receipt_id=payload.receipt_id,
        status=VALID,
        signed_at=utcnow(),
    )
    db.add(sig)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        # 并发撞库：要么对方用了同一回执（幂等返回），要么对方先签成（冲突）
        replay = db.query(Signature).filter_by(receipt_id=payload.receipt_id).one_or_none()
        if replay and replay.consent_version_id == version_id:
            return replay, True
        raise HTTPException(409, "并发冲突：该版本已被他人签署，当前版本只能有一份有效签署")

    surgery = version.surgery
    storage.append_signature_file(version, sig, surgery, surgery.patient)
    notify(
        db,
        surgery.id,
        NOTIFY_CONSENT_SIGNED,
        f"手术「{surgery.title}」同意书 V{version.version_no:02d} 已由 "
        f"{payload.signer_name} 签署，手术准备条件满足",
        recipients=[surgery.surgeon, "scheduler"],
    )
    db.commit()
    db.refresh(sig)
    return sig, False


# ---------------------------------------------------------------- 撤回

def withdraw_consent(db: Session, version_id: int, payload: WithdrawIn) -> ConsentVersion:
    for attempt in range(4):
        try:
            return _withdraw_consent_once(db, version_id, payload)
        except OperationalError as e:
            db.rollback()
            if not _is_lock_error(e) or attempt == 3:
                raise HTTPException(409, "系统繁忙，请刷新后重试")
            time.sleep(0.05 * (attempt + 1))
    raise HTTPException(409, "系统繁忙，请刷新后重试")  # pragma: no cover


def _withdraw_consent_once(db: Session, version_id: int, payload: WithdrawIn) -> ConsentVersion:
    version = get_version_or_404(db, version_id)
    if version.status != ACTIVE:
        raise HTTPException(409, f"版本 V{version.version_no:02d} 已不是当前有效版本，无法撤回")
    if payload.withdrawn_by != version.surgery.patient.name:
        raise HTTPException(403, "仅患者本人可以撤回知情同意")

    valid_sig = (
        db.query(Signature)
        .filter_by(consent_version_id=version.id, status=VALID)
        .one_or_none()
    )
    if not valid_sig:
        raise HTTPException(409, "当前版本尚未签署，无需撤回")

    now = utcnow()
    version.status = WITHDRAWN
    version.withdrawn_at = now
    version.withdrawn_by = payload.withdrawn_by
    version.withdraw_reason = payload.reason or "患者主动撤回"
    valid_sig.status = INVALID
    valid_sig.invalidated_at = now
    valid_sig.invalidate_reason = "患者撤回知情同意"

    surgery = version.surgery
    storage.mark_file_status(version, surgery, surgery.patient)  # 历史文件保留，仅更新状态
    notify(
        db,
        surgery.id,
        NOTIFY_CONSENT_WITHDRAWN,
        f"患者 {payload.withdrawn_by} 撤回了手术「{surgery.title}」的知情同意"
        f"（V{version.version_no:02d}），原文件已失效，请暂停手术安排",
        recipients=[surgery.surgeon, "scheduler"],
    )
    db.commit()
    db.refresh(version)
    return version


# ---------------------------------------------------------------- 排期就绪检查

def readiness(db: Session, surgery_id: int) -> dict:
    surgery = get_surgery_or_404(db, surgery_id)
    current = (
        db.query(ConsentVersion)
        .filter_by(surgery_id=surgery.id, status=ACTIVE)
        .one_or_none()
    )
    base = {"surgery_id": surgery.id, "surgery_title": surgery.title}
    if current:
        valid_sig = (
            db.query(Signature)
            .filter_by(consent_version_id=current.id, status=VALID)
            .one_or_none()
        )
        if valid_sig:
            return {
                **base,
                "ready": True,
                "state": "READY",
                "current_version_id": current.id,
                "current_version_no": current.version_no,
                "signed_by": valid_sig.signer_name,
                "signed_at": valid_sig.signed_at.isoformat(),
                "message": f"已签署当前版本 V{current.version_no:02d}，满足手术准备条件",
            }
        return {
            **base,
            "ready": False,
            "state": "PENDING_SIGNATURE",
            "current_version_id": current.id,
            "current_version_no": current.version_no,
            "signed_by": None,
            "signed_at": None,
            "message": f"当前版本 V{current.version_no:02d} 尚未签署，不可安排手术",
        }
    last = (
        db.query(ConsentVersion)
        .filter_by(surgery_id=surgery.id)
        .order_by(ConsentVersion.version_no.desc())
        .first()
    )
    if last and last.status == WITHDRAWN:
        return {
            **base,
            "ready": False,
            "state": "WITHDRAWN",
            "current_version_id": None,
            "current_version_no": None,
            "signed_by": None,
            "signed_at": None,
            "message": "患者已撤回知情同意，需医生重新出具版本并重新签署",
        }
    return {
        **base,
        "ready": False,
        "state": "NO_VERSION",
        "current_version_id": None,
        "current_version_no": None,
        "signed_by": None,
        "signed_at": None,
        "message": "尚未出具知情同意书，不可安排手术",
    }
