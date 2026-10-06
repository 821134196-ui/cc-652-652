"""请求体模型。"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class PatientIn(BaseModel):
    mrn: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=64)


class SurgeryIn(BaseModel):
    patient_id: int
    title: str = Field(min_length=1, max_length=200)
    surgeon: str = Field(min_length=1, max_length=64)
    scheduled_at: datetime


class ConsentVersionIn(BaseModel):
    procedure: str = Field(min_length=1)   # 术式
    anesthesia: str = Field(min_length=1)  # 麻醉方式
    risks: str = Field(min_length=1)       # 风险
    created_by: str = Field(min_length=1, max_length=64)  # 出具医生


class SignIn(BaseModel):
    signer_name: str = Field(min_length=1, max_length=64)
    signer_role: Literal["PATIENT", "FAMILY"]
    relationship: Optional[str] = None  # 家属必填
    signature_text: str = Field(min_length=1)
    receipt_id: str = Field(min_length=1, max_length=64)


class WithdrawIn(BaseModel):
    withdrawn_by: str = Field(min_length=1, max_length=64)
    reason: Optional[str] = None


class ESignReceiptIn(BaseModel):
    signer_name: str = Field(min_length=1, max_length=64)
    document_hash: str = Field(min_length=1, max_length=64)


class AuthorizedFamilyIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    relationship: str = Field(min_length=1, max_length=64)
