from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import require_admin
from database import get_db
from models import SystemSetting, User

router = APIRouter()
SETTINGS_GROUP = "admin_panel"


class SettingsPayload(BaseModel):
    settings: Dict[str, Any]


@router.get("/settings")
def get_settings(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    rows = db.query(SystemSetting).filter(SystemSetting.group_name == SETTINGS_GROUP).all()
    return {
        "settings": {row.setting_key: row.setting_value for row in rows}
    }


@router.post("/settings")
def save_settings(
    payload: SettingsPayload,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    for key, value in payload.settings.items():
        row = db.query(SystemSetting).filter(
            SystemSetting.group_name == SETTINGS_GROUP,
            SystemSetting.setting_key == key,
        ).first()
        if not row:
            row = SystemSetting(
                group_name=SETTINGS_GROUP,
                setting_key=key,
                setting_value=str(value),
            )
            db.add(row)
        else:
            row.setting_value = str(value)

    db.commit()
    return {"message": "Cilësimet u ruajtën", "saved": len(payload.settings)}
