from document_pipeline_api.public_errors import public_error_message


def test_public_error_keeps_plain_chinese_business_message() -> None:
    assert public_error_message(
        ValueError("当前数据表还没有分 Sheet 视图。"),
        "导出失败，请稍后重试。",
    ) == "当前数据表还没有分 Sheet 视图。"


def test_public_error_hides_technical_details() -> None:
    assert public_error_message(
        RuntimeError("sqlite3.OperationalError: database is locked"),
        "保存失败，请稍后重试。",
    ) == "保存失败，请稍后重试。"

    assert public_error_message(
        RuntimeError("WinError 5 拒绝访问 traceback"),
        "保存失败，请稍后重试。",
    ) == "保存失败，请稍后重试。"
