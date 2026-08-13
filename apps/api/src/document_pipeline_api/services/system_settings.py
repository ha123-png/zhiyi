from sqlalchemy.orm import Session

from document_pipeline_api.models.system_setting import SystemSettingRecord


def get_setting(session: Session, key: str, default: str = "") -> str:
    record = session.get(SystemSettingRecord, key)
    if record is None:
        return default
    return record.value


def set_setting(session: Session, key: str, value: str) -> None:
    record = session.get(SystemSettingRecord, key)
    if record is None:
        session.add(SystemSettingRecord(key=key, value=value))
    else:
        record.value = value


def get_bool_setting(session: Session, key: str, default: bool = True) -> bool:
    raw = get_setting(session, key, "").strip().lower()
    if not raw:
        return default
    return raw == "true"


def set_bool_setting(session: Session, key: str, value: bool) -> None:
    set_setting(session, key, "true" if value else "false")
