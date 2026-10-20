"""Lexim i cilësimeve të admin panelit nga databaza."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from models import SystemSetting

SETTINGS_GROUP = "admin_panel"

DEFAULTS: dict[str, str] = {
    "plagiarism_threshold": "20",
    "api_endpoint": "",
    "report_lang": "Shqip",
}


def get_admin_settings(db: Session) -> dict[str, Any]:
    settings = dict(DEFAULTS)
    rows = (
        db.query(SystemSetting)
        .filter(SystemSetting.group_name == SETTINGS_GROUP)
        .all()
    )
    for row in rows:
        if row.setting_value is not None and str(row.setting_value).strip() != "":
            settings[row.setting_key] = row.setting_value
    return settings


def get_plagiarism_threshold(db: Session) -> float:
    raw = get_admin_settings(db).get("plagiarism_threshold", DEFAULTS["plagiarism_threshold"])
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 30.0
    return max(5.0, min(80.0, value))


def get_warning_threshold(db: Session) -> float:
    """Pragu i zonës 'mesatare' — ~2/3 e pragut kryesor (30→20, 20→13)."""
    return max(5.0, round(get_plagiarism_threshold(db) * 2 / 3, 1))
