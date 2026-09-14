"""Isolated synthetic UI acceptance server; never opens the installed app data."""

from argparse import ArgumentParser
from contextlib import asynccontextmanager
import json
from pathlib import Path

import uvicorn

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import DataTableRecord, DataRowRecord, TaskRecord


class ScriptedConversation:
    """Deterministic native-tool protocol fixture, only installed by this script."""

    def __init__(self, settings, cancel):
        self.cancel = cancel
        self.step = 0

    def close(self):
        pass

    def stream(self, messages, tools):
        question = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        if (
            "模板" in question
            and any(word in question for word in ("创建", "起草", "设计一个"))
            and self.step == 0
        ):
            self.step += 1
            yield {
                "type": "tool",
                "id": "template",
                "name": "propose_operations",
                "arguments": {
                    "title": "创建收支记录模板",
                    "operations": [
                        {
                            "kind": "create_template",
                            "template": {
                                "name": "收支记录模板",
                                "description": "从文件中整理日期、金额和收支类别。",
                                "fields": [
                                    {
                                        "key": "date",
                                        "label": "日期",
                                        "value_type": "date",
                                        "section": "header",
                                        "example": "2026-09-13",
                                    },
                                    {
                                        "key": "amount",
                                        "label": "金额",
                                        "value_type": "number",
                                        "section": "header",
                                        "instructions": "按实际金额填写",
                                    },
                                    {
                                        "key": "category",
                                        "label": "收支类别",
                                        "value_type": "text",
                                        "section": "header",
                                        "example": "收入或支出",
                                    },
                                ],
                                "deterministic_rules": [
                                    {
                                        "kind": "range",
                                        "field": "header.amount",
                                        "minimum": 0,
                                        "severity": "error",
                                    }
                                ],
                                "behavior": {
                                    "presentation": {"mode": "table"},
                                    "requires_complete_input": True,
                                    "suggest_filename": False,
                                },
                            },
                        }
                    ],
                },
            }
            return
        if "修改" in question and self.step == 0:
            self.step += 1
            yield {
                "type": "tool",
                "id": "preview",
                "name": "propose_operations",
                "arguments": {
                    "title": "修改合成采购金额",
                    "operations": [
                        {
                            "kind": "update_rows",
                            "table_id": "ask-synthetic",
                            "row_ids": [1],
                            "changes": {"total": 3600},
                        }
                    ],
                },
            }
            return
        if "模板" in question and "起草" in question and self.step == 0:
            self.step += 1
            yield {
                "type": "tool",
                "id": "draft",
                "name": "draft_template",
                "arguments": {
                    "name": "合成收支记录",
                    "description": "仅供问知意验收",
                    "fields": [
                        {
                            "key": "amount",
                            "label": "金额",
                            "section": "header",
                            "value_type": "number",
                        }
                    ],
                    "extra_instructions": "",
                    "validation_rules": [],
                    "deterministic_rules": [],
                    "output_mapping": {},
                },
            }
            return
        if any(word in question for word in ["分析", "金额", "图"]):
            if self.step == 0:
                self.step += 1
                yield {
                    "type": "tool",
                    "id": "analysis",
                    "name": "analyze_data_table",
                    "arguments": {
                        "table_id": "ask-synthetic",
                        "dimensions": ["date"]
                        if "趋势" in question
                        else []
                        if "总计" in question
                        else ["vendor"],
                        "time_bucket": "month" if "趋势" in question else None,
                        "metrics": [{"op": "sum", "field": "total"}],
                    },
                }
                return
            if self.step == 1:
                self.step += 1
                result = json.loads(messages[-1]["content"])
                if "analysis_id" in result:
                    yield {
                        "type": "tool",
                        "id": "chart",
                        "name": "render_chart",
                        "arguments": {
                            "analysis_id": result["analysis_id"],
                            "type": "area"
                            if "趋势" in question
                            else "donut"
                            if "占比" in question
                            else "metric"
                            if "总计" in question
                            else "horizontal_bar",
                            "title": "采购金额趋势"
                            if "趋势" in question
                            else "各供应方金额",
                            "series": ["sum:total"],
                            "series_labels": {"sum:total": "金额合计"},
                        },
                    }
                    return
        response = "这是合成验收回答。\n\n知意把文件中的信息，整理成**可核对的数据、明确的位置和有意义的文件名**。模板定义你需要什么，以及如何校验和整理。\n\n上面的工具卡保留本次实际结果；修改须在卡片中确认。"
        for i in range(0, len(response), 5):
            if self.cancel.wait(0.025):
                raise InterruptedError("停止")
            yield {"type": "text", "text": response[i : i + 5]}


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--real-model", action="store_true")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    root = args.data_dir.resolve()
    if not root.is_relative_to(project / ".local"):
        parser.error("Synthetic data must live under the workspace .local directory.")
    root.mkdir(parents=True, exist_ok=True)
    app = create_app(
        Settings(
            database_url=f"sqlite:///{root / 'document-pipeline.db'}",
            storage_dir=root / "uploads",
        ),
        web_dir=project / "apps/web/dist",
    )
    lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def seeded(application):
        async with lifespan(application):
            with application.state.session_factory() as session:
                if not session.get(DataTableRecord, "ask-synthetic"):
                    columns = [
                        {
                            "key": "vendor",
                            "label": "供应方",
                            "section": "header",
                            "value_type": "text",
                        },
                        {
                            "key": "total",
                            "label": "含税总额",
                            "section": "header",
                            "value_type": "number",
                        },
                        {
                            "key": "date",
                            "label": "日期",
                            "section": "header",
                            "value_type": "date",
                        },
                    ]
                    session.add(
                        DataTableRecord(
                            id="ask-synthetic",
                            name="问知意合成采购账本",
                            template_key="manual:ask",
                            template_version="1",
                            document_kind="custom",
                            columns_json=json.dumps(columns),
                        )
                    )
                    session.flush()
                    for index, (vendor, total) in enumerate(
                        [
                            ("青禾纸业", 3200),
                            ("山川印务", 4800),
                            ("青禾纸业", 1600),
                            ("松间设计", 6200),
                            ("远山包装", 2900),
                            ("山川印务", 2100),
                        ]
                    ):
                        session.add(
                            DataRowRecord(
                                table_id="ask-synthetic",
                                item_index=index,
                                row_json=json.dumps(
                                    {
                                        "vendor": vendor,
                                        "total": total,
                                        "date": f"2026-0{index + 1}-15",
                                    },
                                    ensure_ascii=False,
                                ),
                                review_pending=False,
                            )
                        )
                    session.commit()
                if not session.get(TaskRecord, "ask-source-image"):
                    from PIL import Image, ImageDraw, ImageFont
                    import hashlib

                    application.state.settings.storage_dir.mkdir(
                        parents=True, exist_ok=True
                    )
                    path = (
                        application.state.settings.storage_dir / "ask-source-image.png"
                    )
                    image = Image.new("RGB", (800, 480), "white")
                    draw = ImageDraw.Draw(image)
                    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 36)
                    draw.text((50, 60), "SYNTHETIC ORDER", font=font, fill="black")
                    draw.text((50, 180), "Order: ZY-2048", font=font, fill="black")
                    draw.text((50, 270), "Total: 88", font=font, fill="black")
                    image.save(path)
                    session.add(
                        TaskRecord(
                            id="ask-source-image",
                            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                            filename="合成订单图.png",
                            storage_path=path.name,
                            content_type="image/png",
                            size_bytes=path.stat().st_size,
                            status="completed",
                            template_mode="auto",
                        )
                    )
                    session.commit()
                if not args.real_model:
                    from document_pipeline_api.services.model_profiles import (
                        list_model_profiles,
                        create_model_profile,
                    )
                    from document_pipeline_api.schemas.model_profiles import (
                        ModelProfileCreate,
                    )
                    from document_pipeline_api.services.system_settings import (
                        set_setting,
                    )

                    profiles = list_model_profiles(session)
                    fixture = next(
                        (p for p in profiles if p.name == "界面验收（模拟回答）"), None
                    )
                    if not fixture:
                        fixture = create_model_profile(
                            session,
                            ModelProfileCreate(
                                name="界面验收（模拟回答）",
                                provider="lm_studio",
                                base_url="http://127.0.0.1:1234/v1",
                                model_name="界面验收 · 模拟回答",
                                context_length=32768,
                            ),
                            None,
                        )
                    set_setting(session, "assistant_model_profile", fixture.id)
                    session.commit()
            yield

    app.router.lifespan_context = seeded
    if not args.real_model:
        app.state.conversation_factory = ScriptedConversation
        app.state.assistant_simulated = True
    uvicorn.run(app, host="127.0.0.1", port=args.port)
