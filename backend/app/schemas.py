"""Pydantic request/response schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ---------- requests ----------

class PatientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    mrn: str = Field(min_length=1, max_length=50)


class FamilyMemberCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    relationship: str = Field(min_length=1, max=50)
    authorized: bool = False
    authorization_note: str | None = Field(default=None, max_length=255)


class FamilyAuthUpdate(BaseModel):
    authorized: bool
    authorization_note: str | None = Field(default=None, max_length=255)


class SurgeryCreate(BaseModel):
    patient_id: int
    name: str = Field(min_length=1, max_length=200)
    scheduled_at: datetime | None = None


class VersionCreate(BaseModel):
    procedure: str = Field(min_length=1, max_length=300, description="术式")
    anesthesia: str = Field(min_length=1, max_length=200, description="麻醉方式")
    risks: str = Field(min_length=1, description="风险告知")
    change_summary: str | None = Field(default=None, max_length=500)
    created_by: str = Field(default="医生", max_length=100)


class SignRequest(BaseModel):
    signer_type: str = Field(pattern="^(PATIENT|FAMILY)$")
    family_id: int | None = None
    # Client-generated key: retries with the same key never create a second file.
    idempotency_key: str | None = Field(default=None, max_length=64)
    # When the (mocked) signature gateway redelivers a receipt, caller echoes it.
    gateway_receipt_id: str | None = Field(default=None, max_length=64)


class WithdrawRequest(BaseModel):
    signer_type: str = Field(pattern="^(PATIENT|FAMILY)$")
    family_id: int | None = None
    reason: str | None = Field(default=None, max_length=500)


# ---------- responses ----------

class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    mrn: str


class FamilyMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    patient_id: int
    name: str
    relationship: str
    authorized: bool
    authorization_note: str | None = None


class SignatureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    signer_type: str
    signer_name: str
    signer_family_id: int | None = None
    gateway_receipt_id: str
    idempotency_key: str | None = None
    document_path: str
    signed_at: datetime
    invalidated_at: datetime | None = None


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    version_no: int
    procedure: str
    anesthesia: str
    risks: str
    change_summary: str | None = None
    status: str
    created_by: str
    created_at: datetime
    superseded_at: datetime | None = None
    withdrawn_at: datetime | None = None
    withdraw_reason: str | None = None
    signature: SignatureOut | None = None


class SurgeryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    patient_id: int
    name: str
    scheduled_at: datetime | None = None
    status: str
    patient: PatientOut


class SurgeryReadiness(BaseModel):
    readiness: str          # READY / RESIGN_REQUIRED / UNSIGNED / WITHDRAWN / NOT_CREATED
    message: str
    active_version_id: int | None = None
    active_version_no: int | None = None


class SurgeryListItem(BaseModel):
    surgery: SurgeryOut
    patient_name: str
    readiness: SurgeryReadiness


class SurgeryDetail(BaseModel):
    surgery: SurgeryOut
    patient: PatientOut
    family_members: list[FamilyMemberOut]
    versions: list[VersionOut]
    readiness: SurgeryReadiness


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    surgery_id: int
    type: str
    message: str
    read: bool
    delivered: bool
    created_at: datetime


class SignResult(BaseModel):
    signature: SignatureOut
    # True when this call created the signature; False for an idempotent replay.
    created: bool
