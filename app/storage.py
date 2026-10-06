"""同意书文件的本地落盘。。历史文件只增不删，保证可追溯。"""
from pathlib import Path

from .config import FILES_DIR, ensure_dirs
from .models import ConsentVersion, Patient, Signature, Surgery

STATUS_CN = {
    "ACTIVE": "当前有效",
    "SUPERSEDED": "已被新版本取代",
    "WITHDRAWN": "患者已撤回",
}


def _fmt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "-"


def render_document(version: ConsentVersion, surgery: Surgery, patient: Patient) -> str:
    return f"""============================================================
                手术知情同意书（版本化存根）
============================================================
文件编号      : S{surgery.id:04d}-V{version.version_no:02d}
内容哈希      : {version.content_hash}
文件状态      : {STATUS_CN.get(version.status, version.status)}

患者姓名      : {patient.name}
病历号        : {patient.mrn}
拟施手术      : {surgery.title}
主刀医生      : {surgery.surgeon}
手术时间      : {_fmt(surgery.scheduled_at)}

一、术式
{version.procedure}

二、麻醉方式
{version.anesthesia}

三、风险告知
{version.risks}

出具医生      : {version.created_by}
出具时间      : {_fmt(version.created_at)}
取代时间      : {_fmt(version.superseded_at)}
撤回时间      : {_fmt(version.withdrawn_at)}
撤回人        : {version.withdrawn_by or '-'}
撤回原因      : {version.withdraw_reason or '-'}
============================================================
"""


def render_signed_appendix(signature: Signature) -> str:
    role = "患者本人" if signature.signer_role == "PATIENT" else f"家属（{signature.relationship_}）"
    return f"""
------------------------ 签署记录 ---------------------------
签署人        : {signature.signer_name}（{role}）
签名内容      : {signature.signature_text}
签名回执号    : {signature.receipt_id}
签署时间      : {_fmt(signature.signed_at)}
签名状态      : {"有效" if signature.status == "VALID" else "已失效"}
失效时间      : {_fmt(signature.invalidated_at)}
失效原因      : {signature.invalidate_reason or '-'}
============================================================
"""


def write_consent_file(version: ConsentVersion, surgery: Surgery, patient: Patient) -> str:
    ensure_dirs()
    name = f"S{surgery.id:04d}_V{version.version_no:02d}_{version.content_hash[:8]}.txt"
    path = Path(FILES_DIR) / name
    path.write_text(render_document(version, surgery, patient), encoding="utf-8")
    return name


def append_signature_file(
    version: ConsentVersion, signature: Signature, surgery: Surgery, patient: Patient
) -> str:
    """签署后生成带签名存根的完整文件（原版本文件保留不动）。"""
    ensure_dirs()
    name = (
        f"S{surgery.id:04d}_V{version.version_no:02d}_"
        f"{version.content_hash[:8]}_signed_{signature.receipt_id[:8]}.txt"
    )
    path = Path(FILES_DIR) / name
    path.write_text(
        render_document(version, surgery, patient) + render_signed_appendix(signature),
        encoding="utf-8",
    )
    return name


def mark_file_status(version: ConsentVersion, surgery: Surgery, patient: Patient) -> None:
    """版本状态变化（被取代/被撤回）时重写存根文件头部状态，历史文件保留。"""
    if version.file_path:
        path = Path(FILES_DIR) / version.file_path
        if path.exists():
            path.write_text(render_document(version, surgery, patient), encoding="utf-8")
