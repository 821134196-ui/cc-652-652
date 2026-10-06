"""ORM 模型。

关键完整性约束（由数据库强制，而非仅靠应用代码）：
- 同一手术最多一条 ACTIVE 版本：部分唯一索引 uq_active_version_per_surgery
- 同一版本最多一条 VALID 签名：部分唯一索引 uq_valid_signature_per_version
- 签名回执全局唯一：receipt_id UNIQUE（重复回执幂等）
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import relationship

from .database import Base

# 版本状态
ACTIVE = "ACTIVE"            # 当前有效版本（每台手术至多一条）
SUPERSEDED = "SUPERSEDED"    # 已被新版本取代
WITHDRAWN = "WITHDRAWN"      # 患者撤回后失效

# 签名状态
VALID = "VALID"
INVALID = "INVALID"

# 签署人角色
ROLE_PATIENT = "PATIENT"
ROLE_FAMILY = "FAMILY"

# 通知类型
NOTIFY_VERSION_CREATED = "VERSION_CREATED"
NOTIFY_VERSION_SUPERSEDED = "VERSION_SUPERSEDED"
NOTIFY_CONSENT_SIGNED = "CONSENT_SIGNED"
NOTIFY_CONSENT_WITHDRAWN = "CONSENT_WITHDRAWN"
NOTIFY_SIGN_REJECTED = "SIGN_REJECTED"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True)
    mrn = Column(String(32), unique=True, nullable=False)  # 病历号
    name = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    authorized_family = relationship(
        "AuthorizedFamily", back_populates="patient", cascade="all, delete-orphan"
    )
    surgeries = relationship("Surgery", back_populates="patient")


class AuthorizedFamily(Base):
    """获授权可代签的家属白名单。"""

    __tablename__ = "authorized_family"
    __table_args__ = (UniqueConstraint("patient_id", "name", name="uq_family_per_patient"),)

    id = Column(Integer, primary_key=True)
    patient_id = Column(ForeignKey("patients.id"), nullable=False)
    name = Column(String(64), nullable=False)
    relationship_ = Column("relationship", String(64), nullable=False)

    patient = relationship("Patient", back_populates="authorized_family")


class Surgery(Base):
    __tablename__ = "surgeries"

    id = Column(Integer, primary_key=True)
    patient_id = Column(ForeignKey("patients.id"), nullable=False)
    title = Column(String(200), nullable=False)
    surgeon = Column(String(64), nullable=False)  # 主刀医生（手术团队通知对象）
    scheduled_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    patient = relationship("Patient", back_populates="surgeries")
    versions = relationship(
        "ConsentVersion", back_populates="surgery", order_by="ConsentVersion.version_no"
    )


class ConsentVersion(Base):
    __tablename__ = "consent_versions"
    __table_args__ = (
        UniqueConstraint("surgery_id", "version_no", name="uq_version_no_per_surgery"),
        Index(
            "uq_active_version_per_surgery",
            "surgery_id",
            unique=True,
            sqlite_where=text("status = 'ACTIVE'"),
        ),
    )

    id = Column(Integer, primary_key=True)
    surgery_id = Column(ForeignKey("surgeries.id"), nullable=False)
    version_no = Column(Integer, nullable=False)
    procedure = Column(Text, nullable=False)      # 术式
    anesthesia = Column(String(200), nullable=False)  # 麻醉方式
    risks = Column(Text, nullable=False)          # 风险告知
    content_hash = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False, default=ACTIVE)
    created_by = Column(String(64), nullable=False)  # 出具医生
    created_at = Column(DateTime, default=utcnow, nullable=False)
    superseded_at = Column(DateTime)
    withdrawn_at = Column(DateTime)
    withdrawn_by = Column(String(64))
    withdraw_reason = Column(Text)
    file_path = Column(String(500))  # 本地文件（相对 FILES_DIR）

    surgery = relationship("Surgery", back_populates="versions")
    signatures = relationship("Signature", back_populates="version")


class Signature(Base):
    __tablename__ = "signatures"
    __table_args__ = (
        Index(
            "uq_valid_signature_per_version",
            "consent_version_id",
            unique=True,
            sqlite_where=text("status = 'VALID'"),
        ),
    )

    id = Column(Integer, primary_key=True)
    consent_version_id = Column(ForeignKey("consent_versions.id"), nullable=False)
    signer_name = Column(String(64), nullable=False)
    signer_role = Column(String(16), nullable=False)  # PATIENT / FAMILY
    relationship_ = Column("relationship", String(64))  # 家属与患者关系
    signature_text = Column(Text, nullable=False)
    receipt_id = Column(String(64), unique=True, nullable=False)  # 回执幂等键
    status = Column(String(16), nullable=False, default=VALID)
    signed_at = Column(DateTime, default=utcnow, nullable=False)
    invalidated_at = Column(DateTime)
    invalidate_reason = Column(String(200))

    version = relationship("ConsentVersion", back_populates="signatures")


class ESignReceipt(Base):
    """本地模拟的外部电子签名服务签发的回执。

    回执绑定 签署人 + 文档内容哈希：旧版本的回执无法用于新版本，
    从机制上阻止"把旧签名复制到新版"。
    """

    __tablename__ = "esign_receipts"

    id = Column(Integer, primary_key=True)
    receipt_id = Column(String(64), unique=True, nullable=False)
    signer_name = Column(String(64), nullable=False)
    document_hash = Column(String(64), nullable=False)
    signature_text = Column(Text, nullable=False)
    issued_at = Column(DateTime, default=utcnow, nullable=False)


class Notification(Base):
    """本地模拟的通知投递记录（同时写入 notifications.log）。"""

    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True)
    surgery_id = Column(ForeignKey("surgeries.id"), nullable=False)
    type = Column(String(32), nullable=False)
    message = Column(Text, nullable=False)
    recipients = Column(String(300), nullable=False)  # 逗号分隔
    created_at = Column(DateTime, default=utcnow, nullable=False)
