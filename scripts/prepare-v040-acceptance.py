"""Prepare a persistent, isolated v0.4.0 acceptance workspace without inference."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.orm import Session

from document_pipeline_api.db import build_engine
from document_pipeline_api.migrations import upgrade_database
from document_pipeline_api.model_secrets import create_model_secret_store
from document_pipeline_api.models import DataRowRecord, DataTableRecord
from document_pipeline_api.schemas.dashboard import CardInput
from document_pipeline_api.schemas.model_profiles import ModelProfileCreate
from document_pipeline_api.schemas.templates import TemplateCreate
from document_pipeline_api.services.dashboard import save_card
from document_pipeline_api.services.model_profiles import (
    activate_model_profile,
    create_model_profile,
    list_model_profiles,
)
from document_pipeline_api.services.system_settings import set_setting
from document_pipeline_api.services.templates import create_template, ensure_builtin_templates

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"
DATA = LOCAL / "acceptance-v0.4.0"
FILES = LOCAL / "acceptance-v0.4.0-files"
# Keep the marker outside the business data: clearing data in the product must
# not cause the next start to silently recreate samples or replace preferences.
MARKER = LOCAL / "acceptance-v0.4.0-prepared.json"
SAMPLE_TABLE_ID = "acceptance-v040-purchases"
CLOUD_NAME = "云端 Qwen 3.6 Flash · 合成验收"


def profile_body(size: str) -> ModelProfileCreate:
    return ModelProfileCreate(
        name=f"本地 Qwen 3.5 {size.upper()} · LM Studio",
        provider="lm_studio", base_url="http://127.0.0.1:1234/v1",
        model_name=f"qwen3.5-{size}", reasoning_effort="none", timeout_seconds=180,
        context_length=8192, temperature=0.1, multimodal=None,
    )


def prepare_inputs(directory: Path) -> list[dict]:
    directory.mkdir(parents=True, exist_ok=False)
    today = date.today()
    samples = [
        {"order_no": "SYN-040-001", "date": today.isoformat(), "supplier": "青禾文具", "amount": 1280, "currency": "CNY", "note": "合成样例：办公用品采购"},
        {"order_no": "SYN-040-002", "date": (today - timedelta(days=2)).isoformat(), "supplier": "澄明设备", "amount": 2460, "currency": "CNY", "note": "合成样例：设备配件采购"},
        {"order_no": "SYN-040-003", "date": (today - timedelta(days=4)).isoformat(), "supplier": "知行材料", "amount": 860, "currency": "CNY", "note": "合成样例：耗材采购"},
    ]
    labels = {"order_no": "采购编号", "date": "日期", "supplier": "供应方", "amount": "金额", "currency": "币种", "note": "备注"}
    for index, sample in enumerate(samples[:2], 1):
        content = "知意 v0.4.0 合成采购单\n此文件仅用于验收，不是真实业务凭证。\n\n"
        content += "\n".join(f"{labels[key]}：{value}" for key, value in sample.items()) + "\n"
        (directory / f"合成采购单-{index:02d}.txt").write_text(content, encoding="utf-8")
    font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"
    if not font_path.is_file():
        raise RuntimeError("缺少微软雅黑字体，尚未生成验收图片；请在当前 Windows 电脑运行。")
    image = Image.new("RGB", (1000, 1150), "white")
    draw = ImageDraw.Draw(image)
    title_font, body_font, small_font = [ImageFont.truetype(str(font_path), size) for size in [42, 30, 22]]
    draw.text((72, 70), "知意 · 合成采购单", font=title_font, fill="#181818")
    draw.text((72, 140), "仅供 v0.4.0 验收，不是真实业务凭证", font=small_font, fill="#666666")
    draw.line((72, 210, 928, 210), fill="#cccccc", width=2)
    for index, (key, value) in enumerate(samples[2].items()):
        y = 260 + index * 104
        draw.text((72, y), labels[key], font=body_font, fill="#666666")
        draw.text((292, y), str(value), font=body_font, fill="#181818")
    draw.text((72, 1000), "原始文件保留；提取结果应由实际模型生成并由人核对。", font=small_font, fill="#666666")
    image.save(directory / "合成采购单-03.png")
    (directory / "预期字段.json").write_text(json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "使用说明.txt").write_text(
        "这些文件都是合成样例。请选择“合成采购单 · 真实提取验收”模板后投入文件。\n"
        "两份 TXT 可先验收文字提取，PNG 再验收视觉能力。\n"
        "预期字段.json 仅供核对，不会作为提取结果导入。\n"
        "若 LM Studio 尚未启动或模型未加载，请先打开本地服务；也可主动选用已授权云端方案。\n"
        "仪表盘中的合成样例表来自手工播种，与这些文件的真实提取结果分开保留。\n",
        encoding="utf-8",
    )
    return samples


def seed_database(directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "uploads").mkdir()
    engine = build_engine(f"sqlite:///{directory / 'document-pipeline.db'}")
    try:
        upgrade_database(engine)
        with Session(engine) as session:
            ensure_builtin_templates(session)
            local4 = create_model_profile(session, profile_body("4b"), None)
            local9 = create_model_profile(session, profile_body("9b"), None)
            activate_model_profile(session, local4.id, 1)
            set_setting(session, "assistant_model_profile", local4.id)
            template = create_template(session, TemplateCreate(
                name="合成采购单 · 真实提取验收",
                description="合成采购凭证的整单信息；需要真实模型读取原件，不预置提取结果。",
                extra_instructions="仅依据原件填写；金额只填数字，币种照原件保留，日期使用 YYYY-MM-DD。每份文件只提取一组公共字段，items 留空。",
                fields=[{"key": key, "label": label, "section": "header", "value_type": value_type}
                        for key, label, value_type in [
                            ("order_no", "采购编号", "text"), ("date", "日期", "date"),
                            ("supplier", "供应方", "text"), ("amount", "金额", "number"),
                            ("currency", "币种", "text"), ("note", "备注", "text"),
                        ]],
            ))
            columns = [
                {"key": key, "label": label, "value_type": value_type, "section": "header"}
                for key, label, value_type in [
                    ("date", "日期", "date"), ("supplier", "供应方", "text"),
                    ("amount", "金额", "number"), ("currency", "币种", "text"),
                    ("note", "备注", "text"),
                ]
            ]
            session.add(DataTableRecord(id=SAMPLE_TABLE_ID, name="采购台账（合成样例）",
                template_key="manual:v040-synthetic-purchases", template_version="1", document_kind="manual",
                columns_json=json.dumps(columns, ensure_ascii=False)))
            session.flush()
            amounts = [1200, 850, 2300, 600, 1550, 980, 1850, 1100, 760, 1680, 1320, 910]
            offsets = [0, 4, 8, 12, 16, 20, 24, 28, 35, 42, 50, 62]
            suppliers = ["青禾文具", "澄明设备", "知行材料"]
            for index, (amount, offset) in enumerate(zip(amounts, offsets)):
                values = {"date": (date.today() - timedelta(days=offset)).isoformat(), "supplier": suppliers[index % 3],
                          "amount": amount, "currency": "CNY", "note": "合成样例 · 手工数据，非 AI 提取"}
                session.add(DataRowRecord(table_id=SAMPLE_TABLE_ID, task_id=None, item_index=0,
                    row_json=json.dumps(values, ensure_ascii=False)))
            session.commit()
        cards = []
        for name, field, bucket in [("月度采购金额（合成样例）", "date", "month"), ("供应方采购金额（合成样例）", "supplier", None)]:
            with Session(engine) as session:
                cards.append(save_card(session, CardInput(name=name, table_id=SAMPLE_TABLE_ID,
                    metric="sum", metric_field="amount", group_field=field, time_bucket=bucket,
                    date_field="date", time_range="all", display="auto"))["id"])
        return {"template_id": template.id, "local_4b_profile_id": local4.id, "local_9b_profile_id": local9.id,
                "sample_table_id": SAMPLE_TABLE_ID, "sample_rows": len(amounts), "sample_total": sum(amounts), "card_ids": cards}
    finally:
        engine.dispose()


def add_cloud_profile(key_file: Path) -> None:
    if not (DATA / "document-pipeline.db").is_file():
        raise RuntimeError("验收库不存在；没有写入云端凭据。")
    engine = build_engine(f"sqlite:///{DATA / 'document-pipeline.db'}")
    try:
        with Session(engine) as session:
            if any(profile.name == CLOUD_NAME for profile in list_model_profiles(session)):
                print("已保留现有云端验收方案，不替换设置或密钥。")
                return
            secret = key_file.read_text(encoding="utf-8-sig").strip()
            if not secret or len(secret) > 2048 or "\n" in secret:
                raise RuntimeError("密钥文件应仅含一行有效 API key；内容未输出。")
            create_model_profile(session, ModelProfileCreate(name=CLOUD_NAME, provider="openai_compatible",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", model_name="qwen3.6-flash",
                timeout_seconds=120, context_length=16384, multimodal=None, temperature=0.1,
                api_key=secret, acknowledge_remote_data_transfer=True), create_model_secret_store())
            print("云端验收方案已添加；密钥保存在 Windows 凭据管理器，未切换默认方案、未发送模型请求。")
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", type=Path, help="显式添加已授权云端方案；默认不读取任何密钥。")
    arguments = parser.parse_args()
    LOCAL.mkdir(parents=True, exist_ok=True)
    if MARKER.exists():
        print("验收环境已经准备过，保留所有用户数据、设置与清空状态。")
    else:
        if DATA.exists() or FILES.exists():
            raise SystemExit("验收目录已存在但没有初始化标记；为保护已有内容，未覆盖。请先核对目录。")
        token = uuid4().hex[:10]
        stage_data = LOCAL / f"acceptance-v0.4.0-preparing-{token}"
        stage_files = LOCAL / f"acceptance-v0.4.0-files-preparing-{token}"
        # On failure, leave the small preparation directory for inspection. Never
        # recursively delete a computed path or retry by overwriting user data.
        samples = prepare_inputs(stage_files)
        metadata = seed_database(stage_data)
        stage_data.replace(DATA)
        stage_files.replace(FILES)
        MARKER.write_text(json.dumps({"version": "0.4.0", "prepared_on": date.today().isoformat(),
            "data_dir": str(DATA), "files_dir": str(FILES), "real_model_calls": 0,
            "input_files": 3, "expected_inputs": samples, **metadata}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"验收环境已准备：{DATA}")
        print(f"合成输入文件：{FILES}")
        print(f"手工样例 {metadata['sample_rows']} 条，金额数值合计 {metadata['sample_total']} CNY；没有伪造提取任务。")
    if arguments.key_file:
        add_cloud_profile(arguments.key_file)
    print("初始方案为本地 Qwen 3.5 4B，后续选择保持用户设置。初始化不加载模型，也不发起推理。")


if __name__ == "__main__":
    main()
