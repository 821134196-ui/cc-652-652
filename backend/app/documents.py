"""Render consent documents to local files.

A signed consent is persisted as a self-contained text document (local storage
per the requirements). Documents are never deleted — superseding a version or
withdrawing consent only invalidates database state; the historical file stays.
"""
from __future__ import annotations

from pathlib import Path

from .database import BASE_DIR, CONSENT_FILE_DIR
from .models import ConsentVersion, Patient, Signature, Surgery, utcnow


def render_document(surgery: Surgery, version: ConsentVersion) -> str:
    patient: Patient = surgery.patient
    lines = [
        "=" * 64,
        "手术知情同意书",
        "=" * 64,
        f"患者：{patient.name}（病历号 {patient.mrn}）",
        f"手术名称：{surgery.name}",
        f"同意书版本：第 {version.version_no} 版",
        "-" * 64,
        "【手术方式（术式）】",
        version.procedure,
        "",
        "【麻醉方式】",
        version.anesthesia,
        "",
        "【风险告知】",
        version.risks,
        "",
        f"文件生成时间：{utcnow().strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "=" * 64,
        "",
    ]
    return "\n".join(lines)


def save_consent_document(version: ConsentVersion, sig: Signature) -> str:
    """Write the immutable signed document; return its relative path token."""
    dir_ = CONSENT_FILE_DIR / f"surgery_{version.surgery_id}"
    dir_.mkdir(parents=True, exist_ok=True)
    ts = sig.signed_at.strftime("%Y%m%dT%H%M%S")
    filename = f"v{version.version_no}_signed_{sig.gateway_receipt_id}.txt"
    path = dir_ / filename
    content = render_document(version.surgery, version)
    invalidated = sig.invalidated_at is not None
    content += (
        f"签署人：{sig.signer_name}（{ '家属' if sig.signer_type == 'FAMILY' else '患者本人'}）\n"
        f"签署时间：{sig.signed_at.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
        f"签名回执号：{sig.gateway_receipt_id}\n"
        f"文件状态：{'已失效（历史留存）' if invalidated else '当前有效'}\n"
    )
    with path.open("w", encoding="utf-8") as f:
        f.write(content)
    # Store a path that resolves regardless of the configured data dir:
    # relative to backend when data lives under it, absolute otherwise.
    try:
        stored = str(path.relative_to(BASE_DIR))
    except ValueError:
        stored = str(path)
    return stored


def write_withdraw_marker(version: ConsentVersion, sig: Signature) -> None:
    """Append a withdrawal record to the retained historical document."""
    rel = Path(sig.document_path)
    # document_path is stored relative to the backend dir: data/consents/...
    from .database import BASE_DIR

    abs_path = BASE_DIR / rel
    with abs_path.open("a", encoding="utf-8") as f:
        f.write(
            "\n" + "-" * 64 + "\n"
            "文件状态（更新）：已失效（历史留存）\n"
            f"【撤回记录】{version.withdraw_reason or '未填写原因'}\n"
            f"撤回时间：{sig.invalidated_at:%Y-%m-%d %H:%M:%S %Z}\n"
            "声明：本同意书自撤回时刻起失效，历史文件按规定留存。\n"
        )
