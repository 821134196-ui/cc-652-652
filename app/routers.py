"""REST API 路由。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from . import services
from .database import get_db
from .models import (
    ACTIVE,
    VALID,
    AuthorizedFamily,
    Notification,
    Patient,
    Surgery,
)
from .schemas import (
    AuthorizedFamilyIn,
    ConsentVersionIn,
    ESignReceiptIn,
    PatientIn,
    SignIn,
    SurgeryIn,
    WithdrawIn,
)

api = APIRouter(prefix="/api")


# ---------------------------------------------------------------- 序列化

def patient_dict(p: Patient) -> dict:
    return {
        "id": p.id,
        "mrn": p.mrn,
        "name": p.name,
        "authorized_family": [
            {"id": f.id, "name": f.name, "relationship": f.relationship_}
            for f in p.authorized_family
        ],
    }


def signature_dict(s) -> dict:
    return {
        "id": s.id,
        "consent_version_id": s.consent_version_id,
        "signer_name": s.signer_name,
        "signer_role": s.signer_role,
        "relationship": s.relationship_,
        "signature_text": s.signature_text,
        "receipt_id": s.receipt_id,
        "status": s.status,
        "signed_at": s.signed_at.isoformat() if s.signed_at else None,
        "invalidated_at": s.invalidated_at.isoformat() if s.invalidated_at else None,
        "invalidate_reason": s.invalidate_reason,
    }


def version_dict(v) -> dict:
    return {
        "id": v.id,
        "surgery_id": v.surgery_id,
        "version_no": v.version_no,
        "procedure": v.procedure,
        "anesthesia": v.anesthesia,
        "risks": v.risks,
        "content_hash": v.content_hash,
        "status": v.status,
        "created_by": v.created_by,
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "superseded_at": v.superseded_at.isoformat() if v.superseded_at else None,
        "withdrawn_at": v.withdrawn_at.isoformat() if v.withdrawn_at else None,
        "withdrawn_by": v.withdrawn_by,
        "withdraw_reason": v.withdraw_reason,
        "file_path": v.file_path,
        "signatures": [signature_dict(s) for s in v.signatures],
    }


def surgery_dict(s: Surgery, readiness: dict | None = None) -> dict:
    d = {
        "id": s.id,
        "title": s.title,
        "surgeon": s.surgeon,
        "scheduled_at": s.scheduled_at.isoformat(),
        "patient": patient_dict(s.patient),
    }
    if readiness is not None:
        d["readiness"] = readiness
    return d


def notification_dict(n: Notification) -> dict:
    return {
        "id": n.id,
        "surgery_id": n.surgery_id,
        "type": n.type,
        "message": n.message,
        "recipients": n.recipients.split(","),
        "created_at": n.created_at.isoformat(),
    }


# ---------------------------------------------------------------- 患者 / 授权家属

@api.get("/patients")
def list_patients(db: Session = Depends(get_db)):
    return [patient_dict(p) for p in db.query(Patient).order_by(Patient.id).all()]


@api.post("/patients", status_code=201)
def create_patient(payload: PatientIn, db: Session = Depends(get_db)):
    if db.query(Patient).filter_by(mrn=payload.mrn).count():
        raise HTTPException(409, f"病历号 {payload.mrn} 已存在")
    p = Patient(mrn=payload.mrn, name=payload.name)
    db.add(p)
    db.commit()
    db.refresh(p)
    return patient_dict(p)


@api.post("/patients/{patient_id}/authorized-family", status_code=201)
def add_authorized_family(patient_id: int, payload: AuthorizedFamilyIn, db: Session = Depends(get_db)):
    patient = db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(404, "患者不存在")
    if any(f.name == payload.name for f in patient.authorized_family):
        raise HTTPException(409, "该家属已在授权名单中")
    f = AuthorizedFamily(patient_id=patient.id, name=payload.name, relationship_=payload.relationship)
    db.add(f)
    db.commit()
    db.refresh(f)
    return {"id": f.id, "name": f.name, "relationship": f.relationship_}


# ---------------------------------------------------------------- 手术 / 排期

@api.get("/surgeries")
def list_surgeries(db: Session = Depends(get_db)):
    surgeries = db.query(Surgery).order_by(Surgery.scheduled_at).all()
    return [surgery_dict(s, services.readiness(db, s.id)) for s in surgeries]


@api.post("/surgeries", status_code=201)
def create_surgery(payload: SurgeryIn, db: Session = Depends(get_db)):
    if not db.get(Patient, payload.patient_id):
        raise HTTPException(404, "患者不存在")
    s = Surgery(
        patient_id=payload.patient_id,
        title=payload.title,
        surgeon=payload.surgeon,
        scheduled_at=payload.scheduled_at,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return surgery_dict(s, services.readiness(db, s.id))


@api.get("/surgeries/{surgery_id}")
def surgery_detail(surgery_id: int, db: Session = Depends(get_db)):
    s = services.get_surgery_or_404(db, surgery_id)
    d = surgery_dict(s, services.readiness(db, surgery_id))
    d["versions"] = [version_dict(v) for v in s.versions]
    return d


@api.get("/surgeries/{surgery_id}/readiness")
def surgery_readiness(surgery_id: int, db: Session = Depends(get_db)):
    return services.readiness(db, surgery_id)


# ---------------------------------------------------------------- 同意书版本

@api.post("/surgeries/{surgery_id}/consent-versions", status_code=201)
def create_version(surgery_id: int, payload: ConsentVersionIn, db: Session = Depends(get_db)):
    return version_dict(services.create_consent_version(db, surgery_id, payload))


@api.post("/consent-versions/{version_id}/sign", status_code=201)
def sign_version(version_id: int, payload: SignIn, db: Session = Depends(get_db)):
    sig, replay = services.sign_consent(db, version_id, payload)
    return {**signature_dict(sig), "idempotent_replay": replay}


@api.post("/consent-versions/{version_id}/withdraw")
def withdraw_version(version_id: int, payload: WithdrawIn, db: Session = Depends(get_db)):
    return version_dict(services.withdraw_consent(db, version_id, payload))


# ---------------------------------------------------------------- 模拟签名服务 / 通知

@api.post("/esign/receipts", status_code=201)
def esign_receipt(payload: ESignReceiptIn, db: Session = Depends(get_db)):
    r = services.issue_esign_receipt(db, payload.signer_name, payload.document_hash)
    return {
        "receipt_id": r.receipt_id,
        "signer_name": r.signer_name,
        "document_hash": r.document_hash,
        "signature_text": r.signature_text,
        "issued_at": r.issued_at.isoformat(),
    }


@api.get("/notifications")
def list_notifications(surgery_id: int | None = None, db: Session = Depends(get_db)):
    q = db.query(Notification).order_by(Notification.id.desc())
    if surgery_id is not None:
        q = q.filter(Notification.surgery_id == surgery_id)
    return [notification_dict(n) for n in q.limit(200).all()]


@api.get("/health")
def health():
    return {"status": "ok"}
