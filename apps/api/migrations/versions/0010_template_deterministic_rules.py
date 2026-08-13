"""Add versioned deterministic template rules.

Revision ID: 0010_template_deterministic_rules
Revises: 0009_extraction_evidence
Create Date: 2026-08-01
"""

from collections.abc import Sequence
import json

from alembic import op
import sqlalchemy as sa


revision: str = "0010_template_deterministic_rules"
down_revision: str | Sequence[str] | None = "0009_extraction_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    existing_columns = {
        column["name"]
        for column in sa.inspect(connection).get_columns("template_versions")
    }
    if "deterministic_rules_json" not in existing_columns:
        with op.batch_alter_table("template_versions") as batch:
            batch.add_column(
                sa.Column(
                    "deterministic_rules_json",
                    sa.Text(),
                    nullable=False,
                    server_default="[]",
                )
            )
    for template_id, rules in (
        ("builtin-invoice", _invoice_rules()),
        ("builtin-delivery", _delivery_rules()),
    ):
        connection.execute(
            sa.text(
                "UPDATE template_versions "
                "SET deterministic_rules_json = :rules "
                "WHERE template_id = :template_id"
            ),
            {
                "rules": json.dumps(rules, ensure_ascii=False),
                "template_id": template_id,
            },
        )


def downgrade() -> None:
    with op.batch_alter_table("template_versions") as batch:
        batch.drop_column("deterministic_rules_json")


def _required_rules() -> list[dict[str, object]]:
    return [
        {
            "kind": "required",
            "field": f"header.{key}",
            "severity": "warning",
        }
        for key in (
            "seller_name",
            "buyer_name",
            "document_number",
            "document_date",
            "total_amount",
        )
    ]


def _line_amount_rule() -> dict[str, object]:
    return {
        "kind": "equation",
        "field": "items[].item_amount",
        "left": {
            "op": "multiply",
            "left": {"op": "field", "path": "items[].quantity"},
            "right": {"op": "field", "path": "items[].unit_price"},
        },
        "right": {"op": "field", "path": "items[].item_amount"},
    }


def _sum_rule(target: str) -> dict[str, object]:
    return {
        "kind": "equation",
        "field": f"header.{target}",
        "left": {"op": "sum", "path": "items[].item_amount"},
        "right": {"op": "field", "path": f"header.{target}"},
    }


def _invoice_rules() -> list[dict[str, object]]:
    return [
        *_required_rules(),
        _line_amount_rule(),
        _sum_rule("amount_before_tax"),
        {
            "kind": "equation",
            "field": "header.total_amount",
            "left": {
                "op": "add",
                "left": {"op": "field", "path": "header.amount_before_tax"},
                "right": {"op": "field", "path": "header.tax_amount"},
            },
            "right": {"op": "field", "path": "header.total_amount"},
        },
    ]


def _delivery_rules() -> list[dict[str, object]]:
    return [*_required_rules(), _line_amount_rule(), _sum_rule("total_amount")]
