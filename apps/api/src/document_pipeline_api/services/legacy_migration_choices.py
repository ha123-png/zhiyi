"""One-time, snapshot-bound migration choices; never a current-data merge policy."""
import hashlib
import json
import math
from datetime import date


def file_fingerprint(connection, task_id: str) -> str:
    """Include legacy facts, evidence references and the contracts interpreting them."""
    snapshot = {}
    for table, column in (('tasks', 'id'), ('extractions', 'task_id'), ('review_revisions', 'task_id'),
                          ('confirmed_documents', 'task_id'), ('data_rows', 'task_id'), ('data_row_revisions', 'task_id')):
        cursor = connection.execute(f'SELECT * FROM {table} WHERE {column}=? ORDER BY rowid', (task_id,))
        names = [column[0] for column in cursor.description]
        snapshot[table] = [dict(zip(names, row)) for row in cursor]
    table_ids = sorted({row['table_id'] for row in snapshot['data_rows']})
    snapshot['tables'] = []
    for identifier in table_ids:
        cursor = connection.execute('SELECT * FROM data_tables WHERE id=?', (identifier,))
        names = [column[0] for column in cursor.description]
        snapshot['tables'].extend(dict(zip(names, row)) for row in cursor)
    # All saved versions matter: an older file can still be bound to their rules.
    cursor = connection.execute('SELECT * FROM template_versions ORDER BY template_id,version')
    names = [column[0] for column in cursor.description]
    snapshot['templates'] = [dict(zip(names, row)) for row in cursor]
    return hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), default=str).encode()).hexdigest()


def validate_choices(connection, report, manifest):
    if manifest is None:
        return {}
    if not isinstance(manifest, dict) or manifest.get('format_version') != 1 or not isinstance(manifest.get('files'), list):
        raise RuntimeError('旧数据迁移选择格式无效。')
    result = {}
    allowed = {'different_current_data', 'header_item_key_collision', 'inconsistent_header',
               'missing_current_rows', 'third_current_snapshot'}
    for choice in manifest['files']:
        task_id = choice.get('task_id') if isinstance(choice, dict) else None
        if not isinstance(task_id, str) or task_id in result:
            raise RuntimeError('迁移选择的文件标识无效或重复。')
        issues = [issue for issue in report['issues'] if issue.get('file_id') == task_id and issue['severity'] == 'blocker']
        if not issues or any(issue['code'] not in allowed for issue in issues):
            raise RuntimeError('迁移选择不能跳过结构损坏或文件归属问题。')
        if choice.get('fingerprint') != file_fingerprint(connection, task_id):
            raise RuntimeError('旧数据在确认迁移选择后发生了变化，请重新核对；未采用过期选择。')
        if type(choice.get('has_data')) is not bool or not isinstance(choice.get('header'), dict) or not isinstance(choice.get('items'), list):
            raise RuntimeError('迁移选择需要完整的文件字段和明细。')
        result[task_id] = choice
    return result


def validate_values(values, columns, *, unchanged=None):
    from document_pipeline_api.services.data_baseline_audit import _same_json
    fields = {column['key']: column for column in columns}
    for key, value in values.items():
        if value is None or value == '':
            continue
        kind = fields[key].get('value_type', 'text')
        valid = not isinstance(value, (dict, list))
        if kind in ('number', 'integer'):
            try:
                valid = type(value) in (int, float) and math.isfinite(value)
                if kind == 'integer' and valid:
                    valid = int(value) == value
            except OverflowError:
                valid = False
        elif kind == 'boolean':
            valid = type(value) is bool
        elif kind == 'text':
            valid = type(value) is str
        elif kind == 'date':
            try:
                date.fromisoformat(value)
            except (TypeError, ValueError):
                valid = False
        # A migration choice must not force rewriting an unrelated legacy value
        # (for example 2017年12月01日). New invalid values are still rejected.
        if not valid and unchanged is not None and key in unchanged and _same_json(value, unchanged[key]):
            continue
        if not valid:
            raise RuntimeError(f'迁移选择的字段“{fields[key].get("label", key)}”类型不正确。')
