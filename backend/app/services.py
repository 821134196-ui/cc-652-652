"""Core business logic.

All multi-step invariants run inside a single transaction:

* new version  -> old ACTIVE version becomes SUPERSEDED and its old signature is
  invalidated *in the same commit* as the new ACTIVE row. The partial UNIQUE
  index ``uq_active_version`` makes it impossible to commit two ACTIVE rows.
* signing       -> the UNIQUE index on signatures(version_id) means a second
  concurrent committer loses with IntegrityError; the loser is treated as an
  idempotent replay and handed back the winner's signature. No second file is
  written and no second receipt is consumed.
* withdrawal    -> flips the version to WITHDRAWN and invalidates the signature
  atomically; the signed file on disk is kept (only appended to).
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import documents, gateways
from .models import (
    NOTIFY_RESIGN_REQUIRED,
    NOTIFY_SIGNED,
    NOTIFY_WITHDRAWN,
    SIGNER_FAMILY,
    SIGNER_PATIENT,
    VERSION_ACTIVE,
    VERSION_SUPERSEDED,
    VERSION_WITHDRAWN,
    ConsentVersion,
    FamilyMember,
    Notification,
    Patient,
    Signature,
    Surgery,
    utcnow,
)
from .schemas import SignRequest, VersionCreate, WithdrawRequest


class ServiceError(Exception):
    def __init__(self, message: str, status_code: int = 400, code: str = "BUSINESS_RULE"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


# ---------------- helpers ----------------

def _notify(db: Session, surgery_id: int, type_: str, message: str) -> None:
    record = Notification(surgery_id=surgery_id, type=type_, message=message, delivered=True)
    db.add(record)
    # Local mock of the external team push channel.
    gateways.notify_surgical_team(surgery_id=surgery_id, type_=type_, message=message)


def get_patient_or_404(db: Session, patient_id: int) -> Patient:
    obj = db.get(Patient, patient_id)
    if obj is None:
        raise ServiceError("患者不存在", 404, "NOT_FOUND")
    return obj


def get_surgery_or_404(db: Session, surgery_id: int) -> Surgery:
    obj = db.get(Surgery, surgery_id)
    if obj is None:
        raise ServiceError("手术不存在", 404, "NOT_FOUND")
    return obj


def get_active_version(db: Session, surgery_id: int) -> ConsentVersion | None:
    stmt = (
        select(ConsentVersion)
        .where(
            ConsentVersion.surgery_id == surgery_id,
            ConsentVersion.status == VERSION_ACTIVE,
        )
    )
    return db.scalars(stmt).one_or_none()


def get_latest_version(db: Session, surgery_id: int) -> ConsentVersion | None:
    stmt = (
        select(ConsentVersion)
        .where(ConsentVersion.surgery_id == surgery_id)
        .order_by(ConsentVersion.version_no.desc())
    )
    return db.scalars(stmt).first()


def _resolve_signer(
    db: Session, patient: Patient, signer_type: str, family_id: int | None
) -> tuple[str, FamilyMember | None]:
    """Validate who is signing/withdrawing. Unauthorized family -> 403."""
    if signer_type == SIGNER_PATIENT:
        return patient.name, None
    if family_id is None:
        raise ServiceError("家属签署必须指定 family_id", 400)
    family = db.get(FamilyMember, family_id)
    if family is None or family.patient_id != patient.id:
        raise ServiceError("家属成员不存在或不属于该患者", 404, "NOT_FOUND")
    if not family.authorized:
        raise ServiceError(
            f"家属「{family.name}」未获授权，不能完成有效签署", 403, "UNAUTHORIZED_FAMILY"
        )
    return family.name, family


# ---------------- version lifecycle ----------------

def create_version(db: Session, surgery_id: int, payload: VersionCreate) -> ConsentVersion:
    surgery = get_surgery_or_404(db, surgery_id)
    now = utcnow()

    next_no = (
        db.scalar(
            select(func.coalesce(func.max(ConsentVersion.version_no), 0)).where(
                ConsentVersion.surgery_id == surgery_id
            )
        )
        + 1
    )

    old_active = get_active_version(db, surgery_id)
    if old_active is not None:
        # Key content changed: old signature no longer satisfies prep conditions.
        old_active.status = VERSION_SUPERSEDED
        old_active.superseded_at = now
        if old_active.signature is not None and old_active.signature.invalidated_at is None:
            old_active.signature.invalidated_at = now

    version = ConsentVersion(
        surgery_id=surgery_id,
        version_no=next_no,
        procedure=payload.procedure,
        anesthesia=payload.anesthesia,
        risks=payload.risks,
        change_summary=payload.change_summary,
        created_by=payload.created_by,
        status=VERSION_ACTIVE,
    )
    db.add(version)
    db.flush()  # get version.id / enforce uq_active_version

    if next_no == 1:
        message = f"手术「{surgery.name}」第 1 版同意书已生成，请安排患者或授权家属签署。"
    else:
        message = (
            f"手术「{surgery.name}」同意书方案已变更为第 {next_no} 版"
            f"（{payload.change_summary or '术式/麻醉/风险关键内容修改'}）。"
            "原签署对当前方案失效，禁止凭旧同意书排期，必须重新签署，旧签名不得复制到新版。"
        )
    _notify(db, surgery_id, NOTIFY_RESIGN_REQUIRED, message)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ServiceError("同意书版本生成冲突，请重试", 409, "VERSION_CONFLICT")
    db.refresh(version)
    return version


# ---------------- signing ----------------

def _find_existing_signature(
    db: Session, version_id: int, payload: SignRequest
) -> Signature | None:
    """Idempotent replay lookup: same idempotency key or same gateway receipt."""
    if payload.idempotency_key:
        sig = db.scalars(
            select(Signature).where(Signature.idempotency_key == payload.idempotency_key)
        ).one_or_none()
        if sig is not None:
            return sig
    if payload.gateway_receipt_id:
        sig = db.scalars(
            select(Signature).where(
                Signature.gateway_receipt_id == payload.gateway_receipt_id
            )
        ).one_or_none()
        if sig is not None:
            return sig
    return None


def sign_active_version(
    db: Session, surgery_id: int, payload: SignRequest
) -> tuple[Signature, bool]:
    """Sign the current ACTIVE version.

    Returns (signature, created). ``created=False`` means an idempotent replay.
    """
    surgery = get_surgery_or_404(db, surgery_id)
    version = get_active_version(db, surgery_id)
    if version is None:
        latest = get_latest_version(db, surgery_id)
        if latest is not None and latest.status == VERSION_WITHDRAWN:
            raise ServiceError("同意已被撤回，不能签署，请联系医生重新生成版本", 409, "WITHDRAWN")
        raise ServiceError("尚未生成同意书版本", 409, "NO_VERSION")

    existing = _find_existing_signature(db, version.id, payload)
    if existing is not None:
        return existing, False

    # Authorization is checked BEFORE any receipt/file side effect.
    signer_name, family = _resolve_signer(
        db, surgery.patient, payload.signer_type, payload.family_id
    )

    receipt_id = payload.gateway_receipt_id or gateways.issue_signature_receipt(
        surgery_id=surgery_id, version_no=version.version_no, signer_name=signer_name
    )

    sig = Signature(
        version_id=version.id,
        signer_type=payload.signer_type,
        signer_patient_id=surgery.patient_id if payload.signer_type == SIGNER_PATIENT else None,
        signer_family_id=family.id if family is not None else None,
        signer_name=signer_name,
        gateway_receipt_id=receipt_id,
        idempotency_key=payload.idempotency_key,
        document_path="",  # filled after flush, once signed_at exists
    )
    db.add(sig)
    try:
        db.flush()  # enforces one-signature-per-version under concurrency
    except IntegrityError:
        # Concurrent signer won the race, or the receipt/key was consumed in a
        # parallel transaction. Roll back and return the winner — never a 2nd file.
        db.rollback()
        winner = db.scalars(
            select(Signature).where(Signature.version_id == version.id)
        ).one_or_none()
        if winner is None and payload.gateway_receipt_id:
            winner = db.scalars(
                select(Signature).where(
                    Signature.gateway_receipt_id == payload.gateway_receipt_id
                )
            ).one_or_none()
        if winner is None and payload.idempotency_key:
            winner = db.scalars(
                select(Signature).where(
                    Signature.idempotency_key == payload.idempotency_key
                )
            ).one_or_none()
        if winner is None:
            raise ServiceError("签署冲突且无法定位既有签名，请刷新后重试", 409, "SIGN_CONFLICT")
        return winner, False

    version.surgery = surgery  # make relationship available to renderer
    sig.document_path = documents.save_consent_document(version, sig)

    _notify(
        db,
        surgery_id,
        NOTIFY_SIGNED,
        f"手术「{surgery.name}」第 {version.version_no} 版同意书已由{signer_name}完成有效签署"
        f"（回执 {receipt_id}），当前版本具备排期准备条件。",
    )
    db.commit()
    db.refresh(sig)
    return sig, True


# ---------------- withdrawal ----------------

def withdraw_consent(db: Session, surgery_id: int, payload: WithdrawRequest) -> ConsentVersion:
    surgery = get_surgery_or_404(db, surgery_id)
    version = get_active_version(db, surgery_id)
    if version is None:
        raise ServiceError("当前没有可撤回的有效同意书版本", 409, "NO_ACTIVE_VERSION")

    signer_name, family = _resolve_signer(
        db, surgery.patient, payload.signer_type, payload.family_id
    )
    now = utcnow()
    version.status = VERSION_WITHDRAWN
    version.withdrawn_at = now
    version.withdraw_reason = payload.reason

    sig = version.signature
    file_marker_needed = False
    if sig is not None:
        sig.invalidated_at = now
        version.surgery = surgery
        file_marker_needed = True

    _notify(
        db,
        surgery_id,
        NOTIFY_WITHDRAWN,
        f"手术「{surgery.name}」第 {version.version_no} 版同意书已被{signer_name}撤回"
        f"（原因：{payload.reason or '未填写'}）。原文件即刻失效并停止排期，历史文件按规定留存。",
    )
    db.commit()
    # Append to the retained file only after the DB commit succeeds.
    if file_marker_needed:
        documents.write_withdraw_marker(version, sig)
    db.refresh(version)
    return version


# ---------------- readiness ----------------

def assess_readiness(db: Session, surgery: Surgery) -> dict:
    """Single source of truth for what the scheduling page must show."""
    latest = get_latest_version(db, surgery.id)
    if latest is None:
        return {
            "readiness": "NOT_CREATED",
            "message": "尚未生成同意书，不能安排手术。",
            "active_version_id": None,
            "active_version_no": None,
        }

    if latest.status == VERSION_WITHDRAWN:
        return {
            "readiness": "WITHDRAWN",
            "message": "患者/授权家属已撤回同意，原文件已失效，禁止排期。",
            "active_version_id": None,
            "active_version_no": None,
        }

    if latest.status == VERSION_SUPERSEDED:
        return {
            "readiness": "RESIGN_REQUIRED",
            "message": "手术方案已变更，当前无有效签署版本，须重新签署后方可排期。",
            "active_version_id": None,
            "active_version_no": None,
        }

    # latest is ACTIVE
    sig = latest.signature
    any_prior_signature = db.scalar(
        select(func.count(Signature.id))
        .join(ConsentVersion, Signature.version_id == ConsentVersion.id)
        .where(ConsentVersion.surgery_id == surgery.id)
    )
    if sig is not None and sig.invalidated_at is None:
        return {
            "readiness": "READY",
            "message": f"当前第 {latest.version_no} 版同意书已完成有效签署，可安排手术。",
            "active_version_id": latest.id,
            "active_version_no": latest.version_no,
        }
    if any_prior_signature:
        return {
            "readiness": "RESIGN_REQUIRED",
            "message": (
                f"方案已变更为第 {latest.version_no} 版，旧版签名不适用于当前版本，"
                "请重新签署，不能把旧签名复制到新版。"
            ),
            "active_version_id": latest.id,
            "active_version_no": latest.version_no,
        }
    return {
        "readiness": "UNSIGNED",
        "message": f"第 {latest.version_no} 版同意书已生成，等待患者或授权家属签署。",
        "active_version_id": latest.id,
        "active_version_no": latest.version_no,
    }
