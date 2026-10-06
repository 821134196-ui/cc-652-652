"""HTTP API."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import services
from .database import get_db, init_db
from .models import (
    FamilyMember,
    Notification,
    Patient,
    Surgery,
)
from .schemas import (
    FamilyAuthUpdate,
    FamilyMemberCreate,
    FamilyMemberOut,
    NotificationOut,
    PatientCreate,
    PatientOut,
    SurgeryCreate,
    SurgeryDetail,
    SurgeryListItem,
    SurgeryOut,
    SignRequest,
    SignResult,
    VersionCreate,
    VersionOut,
    WithdrawRequest,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="手术知情同意版本与撤回系统", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _raise(err: services.ServiceError) -> None:
    raise HTTPException(status_code=err.status_code, detail={"code": err.code, "message": err.message})


def _family_out(f: FamilyMember) -> FamilyMemberOut:
    return FamilyMemberOut(
        id=f.id,
        patient_id=f.patient_id,
        name=f.name,
        relationship=f.relation,
        authorized=f.authorized,
        authorization_note=f.authorization_note,
    )


# ---------------- patients / family ----------------

@app.post("/api/patients", response_model=PatientOut, tags=["patients"])
def create_patient(payload: PatientCreate, db: Session = Depends(get_db)):
    existing = db.scalars(select(Patient).where(Patient.mrn == payload.mrn)).one_or_none()
    if existing is not None:
        raise HTTPException(409, detail={"code": "DUP_MRN", "message": "病历号已存在"})
    patient = Patient(**payload.model_dump())
    db.add(patient)
    db.commit()
    db.refresh(patient)
    return patient


@app.get("/api/patients", response_model=list[PatientOut], tags=["patients"])
def list_patients(db: Session = Depends(get_db)):
    return list(db.scalars(select(Patient).order_by(Patient.id)).all())


@app.post("/api/patients/{patient_id}/family", response_model=FamilyMemberOut, tags=["patients"])
def add_family(patient_id: int, payload: FamilyMemberCreate, db: Session = Depends(get_db)):
    services.get_patient_or_404(db, patient_id)
    dup = db.scalars(
        select(FamilyMember).where(
            FamilyMember.patient_id == patient_id, FamilyMember.name == payload.name
        )
    ).one_or_none()
    if dup is not None:
        raise HTTPException(409, detail={"code": "DUP_FAMILY", "message": "该家属已登记"})
    family = FamilyMember(
        patient_id=patient_id,
        name=payload.name,
        relation=payload.relationship,
        authorized=payload.authorized,
        authorization_note=payload.authorization_note,
    )
    db.add(family)
    db.commit()
    db.refresh(family)
    return _family_out(family)


@app.patch("/api/family/{family_id}/authorization", response_model=FamilyMemberOut, tags=["patients"])
def set_family_authorization(family_id: int, payload: FamilyAuthUpdate, db: Session = Depends(get_db)):
    family = db.get(FamilyMember, family_id)
    if family is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "家属不存在"})
    family.authorized = payload.authorized
    family.authorization_note = payload.authorization_note
    db.commit()
    db.refresh(family)
    return _family_out(family)


# ---------------- surgeries ----------------

@app.post("/api/surgeries", response_model=SurgeryOut, tags=["surgeries"])
def create_surgery(payload: SurgeryCreate, db: Session = Depends(get_db)):
    services.get_patient_or_404(db, payload.patient_id)
    surgery = Surgery(**payload.model_dump())
    db.add(surgery)
    db.commit()
    db.refresh(surgery)
    return surgery


@app.get("/api/surgeries", response_model=list[SurgeryListItem], tags=["surgeries"])
def list_surgeries(db: Session = Depends(get_db)):
    surgeries = list(db.scalars(select(Surgery).order_by(Surgery.id)).all())
    result = []
    for s in surgeries:
        result.append(
            SurgeryListItem(
                surgery=SurgeryOut.model_validate(s),
                patient_name=s.patient.name,
                readiness=services.assess_readiness(db, s),
            )
        )
    return result


@app.get("/api/surgeries/{surgery_id}", response_model=SurgeryDetail, tags=["surgeries"])
def get_surgery(surgery_id: int, db: Session = Depends(get_db)):
    surgery = services.get_surgery_or_404(db, surgery_id)
    family = list(
        db.scalars(
            select(FamilyMember)
            .where(FamilyMember.patient_id == surgery.patient_id)
            .order_by(FamilyMember.id)
        ).all()
    )
    return SurgeryDetail(
        surgery=SurgeryOut.model_validate(surgery),
        patient=PatientOut.model_validate(surgery.patient),
        family_members=[_family_out(f) for f in family],
        versions=[VersionOut.model_validate(v) for v in surgery.versions],
        readiness=services.assess_readiness(db, surgery),
    )


@app.get("/api/surgeries/{surgery_id}/readiness", tags=["surgeries"])
def get_readiness(surgery_id: int, db: Session = Depends(get_db)):
    surgery = services.get_surgery_or_404(db, surgery_id)
    return services.assess_readiness(db, surgery)


# ---------------- consent versions / signing / withdrawal ----------------

@app.post("/api/surgeries/{surgery_id}/versions", response_model=VersionOut, tags=["consent"])
def create_version(surgery_id: int, payload: VersionCreate, db: Session = Depends(get_db)):
    try:
        version = services.create_version(db, surgery_id, payload)
    except services.ServiceError as e:
        _raise(e)
    return VersionOut.model_validate(version)


@app.post("/api/surgeries/{surgery_id}/sign", response_model=SignResult, tags=["consent"])
def sign(surgery_id: int, payload: SignRequest, db: Session = Depends(get_db)):
    try:
        sig, created = services.sign_active_version(db, surgery_id, payload)
    except services.ServiceError as e:
        _raise(e)
    return SignResult(signature=sig, created=created)


@app.post("/api/surgeries/{surgery_id}/withdraw", response_model=VersionOut, tags=["consent"])
def withdraw(surgery_id: int, payload: WithdrawRequest, db: Session = Depends(get_db)):
    try:
        version = services.withdraw_consent(db, surgery_id, payload)
    except services.ServiceError as e:
        _raise(e)
    return VersionOut.model_validate(version)


# ---------------- notifications ----------------

@app.get("/api/notifications", response_model=list[NotificationOut], tags=["notifications"])
def list_notifications(surgery_id: int | None = None, db: Session = Depends(get_db)):
    stmt = select(Notification).order_by(Notification.id.desc())
    if surgery_id is not None:
        stmt = stmt.where(Notification.surgery_id == surgery_id)
    return list(db.scalars(stmt).all())


@app.post("/api/notifications/{notification_id}/read", response_model=NotificationOut, tags=["notifications"])
def mark_read(notification_id: int, db: Session = Depends(get_db)):
    n = db.get(Notification, notification_id)
    if n is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "通知不存在"})
    n.read = True
    db.commit()
    db.refresh(n)
    return n


@app.get("/api/health", tags=["meta"])
def health():
    return {"status": "ok"}
