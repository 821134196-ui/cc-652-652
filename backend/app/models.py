"""ORM models.

Invariants (enforced by the database, not just application code):

* ``uq_active_version`` — a surgery has AT MOST ONE active consent version.
  Creating a new version supersedes the old one in the same transaction.
* ``uq_signature_per_version`` — a version can be signed only once, so two
  concurrent signers (patient + family, or a duplicated gateway callback) can
  never produce two effective signature records / two valid files.
* ``uq_receipt`` — a signature gateway receipt is consumed exactly once;
  redelivering the same receipt is idempotent.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

# --- consent version lifecycle ---
VERSION_ACTIVE = "ACTIVE"        # current version; signature may or may not exist
VERSION_SUPERSEDED = "SUPERSEDED"  # replaced by a newer version, signature no longer qualifies
VERSION_WITHDRAWN = "WITHDRAWN"  # consent withdrawn; file retained but invalid

# --- surgery scheduling status ---
SURGERY_SCHEDULED = "SCHEDULED"
SURGERY_PERFORMED = "PERFORMED"
SURGERY_CANCELLED = "CANCELLED"

# --- signer ---
SIGNER_PATIENT = "PATIENT"
SIGNER_FAMILY = "FAMILY"

# --- notification types ---
NOTIFY_SIGNED = "SIGNED"
NOTIFY_RESIGN_REQUIRED = "RESIGN_REQUIRED"
NOTIFY_WITHDRAWN = "WITHDRAWN"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    mrn: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)  # medical record number
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    family_members: Mapped[list["FamilyMember"]] = relationship(
        back_populates="patient", cascade="all, delete-orphan"
    )
    surgeries: Mapped[list["Surgery"]] = relationship(
        back_populates="patient", cascade="all, delete-orphan"
    )


class FamilyMember(Base):
    __tablename__ = "family_members"
    __table_args__ = (
        UniqueConstraint("patient_id", "name", name="uq_family_patient_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Attribute is renamed from the column name: `relationship` would shadow
    # SQLAlchemy's relationship() inside the class body.
    relation: Mapped[str] = mapped_column("relationship", String(50), nullable=False)
    authorized: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    authorization_note: Mapped[str | None] = mapped_column(String(255))

    patient: Mapped[Patient] = relationship(back_populates="family_members")


class Surgery(Base):
    __tablename__ = "surgeries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)  # e.g. 腹腔镜胆囊切除术
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default=SURGERY_SCHEDULED, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    patient: Mapped[Patient] = relationship(back_populates="surgeries")
    versions: Mapped[list["ConsentVersion"]] = relationship(
        back_populates="surgery",
        cascade="all, delete-orphan",
        order_by="ConsentVersion.version_no",
    )


class ConsentVersion(Base):
    __tablename__ = "consent_versions"
    __table_args__ = (
        UniqueConstraint("surgery_id", "version_no", name="uq_version_no"),
        # Hard guarantee: at most one ACTIVE version per surgery.
        Index(
            "uq_active_version",
            "surgery_id",
            unique=True,
            sqlite_where=text("status = 'ACTIVE'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    surgery_id: Mapped[int] = mapped_column(ForeignKey("surgeries.id"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    procedure: Mapped[str] = mapped_column(String(300), nullable=False)       # 术式
    anesthesia: Mapped[str] = mapped_column(String(200), nullable=False)      # 麻醉方式
    risks: Mapped[str] = mapped_column(Text, nullable=False)                  # 风险告知
    change_summary: Mapped[str | None] = mapped_column(String(500))           # 本次修改说明
    status: Mapped[str] = mapped_column(String(20), default=VERSION_ACTIVE, nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), default="医生")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime)
    withdraw_reason: Mapped[str | None] = mapped_column(String(500))

    surgery: Mapped[Surgery] = relationship(back_populates="versions")
    signature: Mapped["Signature | None"] = relationship(
        back_populates="version", cascade="all, delete-orphan", uselist=False
    )


class Signature(Base):
    __tablename__ = "signatures"
    __table_args__ = (
        # Hard guarantee: one version -> at most one signature row.
        Index("uq_signature_per_version", "version_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("consent_versions.id"), nullable=False
    )
    signer_type: Mapped[str] = mapped_column(String(10), nullable=False)  # PATIENT / FAMILY
    signer_patient_id: Mapped[int | None] = mapped_column(ForeignKey("patients.id"))
    signer_family_id: Mapped[int | None] = mapped_column(ForeignKey("family_members.id"))
    signer_name: Mapped[str] = mapped_column(String(100), nullable=False)
    gateway_receipt_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    document_path: Mapped[str] = mapped_column(String(500), nullable=False)
    signed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime)  # set on withdraw; file kept

    version: Mapped[ConsentVersion] = relationship(back_populates="signature")


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    surgery_id: Mapped[int] = mapped_column(ForeignKey("surgeries.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
