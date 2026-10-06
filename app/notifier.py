"""本地模拟的通知服务：写 notifications 表 + 追加 notifications.log。"""
import json
from pathlib import Path

from sqlalchemy.orm import Session

from .config import FILES_DIR, ensure_dirs
from .models import Notification, utcnow


def notify(db: Session, surgery_id: int, type_: str, message: str, recipients: list[str]) -> Notification:
    ensure_dirs()
    n = Notification(
        surgery_id=surgery_id,
        type=type_,
        message=message,
        recipients=",".join(recipients),
        created_at=utcnow(),
    )
    db.add(n)
    db.flush()  # 拿到 id，随调用方事务一起提交

    log_line = json.dumps(
        {
            "id": n.id,
            "surgery_id": surgery_id,
            "type": type_,
            "message": message,
            "recipients": recipients,
            "created_at": n.created_at.isoformat(),
        },
        ensure_ascii=False,
    )
    with (Path(FILES_DIR) / "notifications.log").open("a", encoding="utf-8") as f:
        f.write(log_line + "\n")
    return n
