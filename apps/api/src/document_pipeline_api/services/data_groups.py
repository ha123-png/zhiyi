"""Stable identity for real files, independent source groups and manual rows."""
import json

from sqlalchemy import case, func
from fastapi import HTTPException

from document_pipeline_api.models import DataRowRecord


def parse_source_key(encoded: str) -> str:
    try:
        value = json.loads(encoded)
        if not isinstance(value, dict) or set(value) != {'kind', 'id'}:
            raise ValueError()
        if value['kind'] == 'row':
            if type(value['id']) is not int or value['id'] <= 0:
                raise ValueError()
        elif value['kind'] in ('file', 'group'):
            if not isinstance(value['id'], str) or not value['id'] or len(value['id']) > 1024:
                raise ValueError()
        else:
            raise ValueError()
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    except (ValueError, TypeError):
        raise HTTPException(422, '来源标识无效，请重新选择。') from None


def validate_source_keys(session, table_id, keys):
    """An empty selection stays empty; stale/foreign identities never widen it."""
    if keys is None:
        return None
    keys = sorted({parse_source_key(key) for key in keys})
    identity = source_identity_expression()
    from sqlalchemy import select
    found = set(session.scalars(select(identity).where(
        DataRowRecord.table_id == table_id, identity.in_(keys)).distinct()))
    if found != set(keys):
        raise HTTPException(409, '选中的文件或来源已变化，请刷新后重新选择。')
    return keys


def source_identity_expression():
    group = func.json_extract(DataRowRecord.row_json, '$.__row_group')
    return case(
        (DataRowRecord.task_id.is_not(None), func.json_object('id', DataRowRecord.task_id, 'kind', 'file')),
        ((func.json_type(DataRowRecord.row_json, '$.__row_group') == 'text') & (group != ''),
         func.json_object('id', group, 'kind', 'group')),
        else_=func.json_object('id', DataRowRecord.id, 'kind', 'row'),
    )


def source_view_value(encoded):
    identity = json.loads(encoded)
    # Existing file group IDs remain usable; independent groups are explicit.
    return identity['id'] if identity['kind'] == 'file' else identity


def shared_header_values(session, table_id, keys, columns):
    """Only uniform, typed independent values represent a shared header."""
    from sqlalchemy import select
    headers = [column for column in columns if column.section == 'header']
    identity = source_identity_expression()
    expressions = []
    for column in headers:
        path = '$."' + column.key.replace('\\', '\\\\').replace('"', '\\"') + '"'
        encoded = func.json_array(func.coalesce(func.json_type(DataRowRecord.row_json, path), 'null'),
                                  func.json_extract(DataRowRecord.row_json, path))
        expressions.extend([func.count(func.distinct(encoded)), func.min(encoded)])
    result = {}
    for key, *cells in session.execute(select(identity, *expressions).where(
            DataRowRecord.table_id == table_id, DataRowRecord.task_id.is_(None), identity.in_(keys)).group_by(identity)):
        result[key] = {}
        for index, column in enumerate(headers):
            variants, encoded = cells[index * 2:index * 2 + 2]
            if variants == 1:
                kind, value = json.loads(encoded)
                result[key][column.key] = True if kind == 'true' else False if kind == 'false' else value
    return result


def row_source_key(row):
    values = json.loads(row.row_json)
    group = values.get('__row_group')
    return json.dumps({'id': row.task_id or (group if isinstance(group, str) and group else row.id),
                       'kind': 'file' if row.task_id else 'group' if isinstance(group, str) and group else 'row'},
                      ensure_ascii=False, separators=(',', ':'))


def effective_header_keys(session, row, columns):
    if row.task_id:
        return {column.key for column in columns if column.section == 'header'}
    key = row_source_key(row)
    return set(shared_header_values(session, row.table_id, [key], columns).get(key, {}))
