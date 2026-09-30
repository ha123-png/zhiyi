"""Paginate source groups first, then their details; cards never split a file."""
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import case, func, select

from document_pipeline_api.models import DataRowRecord, FileCurrentDataRecord
from document_pipeline_api.services.data_groups import source_identity_expression, parse_source_key, shared_header_values
from document_pipeline_api.services.data_rows import data_row_read
from document_pipeline_api.services.table_scope import prepare_export_selection
from document_pipeline_api.schemas.json_numbers import stringify_unsafe_integers


def _headers(session, table_id, keys, columns):
    """Only collapse genuinely shared independent cells; never take a first row."""
    identity = source_identity_expression()
    values = DataRowRecord.row_json
    header_columns = [column for column in columns if column.section == 'header']
    statement = select(identity, func.count(), func.min(DataRowRecord.id),
        func.min(func.json_extract(values, '$.source_filename')),
        func.sum(case((DataRowRecord.is_header_only.is_(False), 1), else_=0)),
        func.max(case((DataRowRecord.review_pending.is_(True), 1), else_=0)),
        func.max(case((func.json_extract(DataRowRecord.input_scope_json, '$.coverage') == 'partial', 1), else_=0))
    ).where(DataRowRecord.table_id == table_id, identity.in_(keys)).group_by(identity)
    result = {}
    for row in session.execute(statement):
        key, count, first_id, filename, items, pending, partial = row
        source = json.loads(key)
        result[key] = {'id': key, 'kind': source['kind'],
            'task_id': source['id'] if source['kind'] == 'file' else None,
            'filename': filename or (f'独立数据组 · {first_id}' if source['kind'] == 'group' else f'手工记录 · {first_id}'),
            'first_row_id': first_id, 'row_count': count, 'item_count': items, 'review_pending': bool(pending),
            'partial_input': bool(partial),
            'header': {}, 'shared_header_keys': []}
    for key, header in shared_header_values(session, table_id, keys, columns).items():
        result[key]['header'] = header
        result[key]['shared_header_keys'] = list(header)
    files = {card['task_id']: card for card in result.values() if card['task_id']}
    for current in session.scalars(select(FileCurrentDataRecord).where(FileCurrentDataRecord.task_id.in_(files))):
        files[current.task_id]['header'] = json.loads(current.header_json)
        files[current.task_id]['filename'] = current.source.display_filename
        files[current.task_id]['shared_header_keys'] = [column.key for column in header_columns]
        if not columns:
            # Autonomous files have individual contracts, never a table-wide union.
            files[current.task_id]['schema'] = json.loads(current.schema_json)
            files[current.task_id]['shared_header_keys'] = [column['key'] for column in files[current.task_id]['schema']
                                                           if column.get('section') == 'header']
    for card in result.values():
        card.setdefault('schema', [{**column.model_dump(), 'section': 'header' if column.key in card['shared_header_keys'] else 'item'} for column in columns])
    # A bounded stream of identities/versions detects unseen edits or additions
    # before a whole-card action. Business values are not loaded for this token.
    digests = {key: hashlib.sha256(json.dumps([column.model_dump() for column in columns],
        sort_keys=True, ensure_ascii=False).encode()) for key in keys}
    for key, row_id, version in session.execute(select(identity, DataRowRecord.id, DataRowRecord.row_version)
            .where(DataRowRecord.table_id == table_id, identity.in_(keys))
            .order_by(DataRowRecord.id).execution_options(yield_per=1000)):
        digests[key].update(f'{row_id}:{version};'.encode())
    for key, card in result.items():
        digests[key].update(json.dumps(card['schema'], sort_keys=True, ensure_ascii=False).encode())
        card['version'] = digests[key].hexdigest()
    return result


def _preview_fields(columns, values, search=''):
    selected = []
    query = search.strip().casefold()
    if query:
        columns = sorted(columns, key=lambda column: query not in str(values.get(column['key'], '')).casefold())
    for column in columns:
        value = values.get(column['key'])
        if value is None or value == '':
            continue
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        truncated = isinstance(value, str) and len(value) > 180
        if truncated:
            index = value.casefold().find(query) if query else -1
            start = max(0, index - 35) if index >= 0 else 0
            value = ('…' if start else '') + value[start:start + 180] + ('…' if start + 180 < len(value) else '')
        selected.append({'key': column['key'], 'label': column['label'],
                         'value': value, 'truncated': truncated})
        if len(selected) == 3:
            break
    return selected


def list_source_cards(session, table_id, *, scope=None, page=1, page_size=6):
    from document_pipeline_api.services.data_tables import table_columns
    selection = prepare_export_selection(session, table_id, scope)
    identity = source_identity_expression()
    grouped = select(identity.label('source_key'), func.min(DataRowRecord.id).label('first_id'),
        func.count().label('matches')).where(*selection.predicates).group_by(identity).subquery()
    total = session.scalar(select(func.count()).select_from(grouped)) or 0
    groups = session.execute(select(grouped).order_by(grouped.c.first_id)
        .offset((page - 1) * page_size).limit(page_size)).all()
    if not groups:
        return stringify_unsafe_integers({'table_id': table_id, 'page': page, 'page_size': page_size, 'total': total,
                'row_count': selection.row_count, 'groups': []})
    keys = [group.source_key for group in groups]
    cards = _headers(session, table_id, keys, table_columns(session, selection.table))
    ranked = select(DataRowRecord.id, func.row_number().over(partition_by=identity,
        order_by=(DataRowRecord.item_index, DataRowRecord.id)).label('position')).where(
            *selection.predicates, identity.in_(keys), DataRowRecord.is_header_only.is_(False)).subquery()
    previews = {key: [] for key in keys}
    for key, row in session.execute(select(identity, DataRowRecord).join(ranked, ranked.c.id == DataRowRecord.id)
            .where(ranked.c.position <= 3).order_by(DataRowRecord.item_index, DataRowRecord.id)):
        previews[key].append({'id': row.id, 'item_index': row.item_index,
            'fields': _preview_fields([column for column in cards[key]['schema'] if column['section'] == 'item'],
                                      json.loads(row.row_json), scope.search if scope else '')})
    for group in groups:
        card = cards[group.source_key]
        card['matched_row_count'] = group.matches
        card['complete'] = group.matches == card['row_count']
        card['preview_items'] = previews[group.source_key]
        card['preview_header'] = _preview_fields([column for column in card['schema'] if column['section'] == 'header'], card['header'], scope.search if scope else '')
        del card['header'], card['schema']
    return stringify_unsafe_integers({'table_id': table_id, 'page': page, 'page_size': page_size, 'total': total,
            'row_count': selection.row_count, 'groups': [cards[key] for key in keys]})


def source_card_details(session, table_id, source_key, *, scope=None, page=1, page_size=20):
    from document_pipeline_api.services.data_tables import table_columns
    selection = prepare_export_selection(session, table_id, scope)
    source_key = parse_source_key(source_key)
    identity = source_identity_expression()
    predicates = [*selection.predicates, identity == source_key]
    total = session.scalar(select(func.count()).select_from(DataRowRecord).where(*predicates)) or 0
    if not total:
        raise HTTPException(404, '当前范围内没有这个来源的数据，请刷新。')
    card = _headers(session, table_id, [source_key], table_columns(session, selection.table))[source_key]
    # Header-only carrier rows are editable source records, not fake details.
    rows = session.scalars(select(DataRowRecord).where(*predicates).order_by(DataRowRecord.item_index, DataRowRecord.id)
        .offset((page - 1) * page_size).limit(page_size)).all()
    return stringify_unsafe_integers({**card, 'page': page, 'page_size': page_size, 'total': total,
            'matched_row_count': total, 'complete': total == card['row_count'],
            'rows': [data_row_read(row).model_dump(mode='json') for row in rows]})


def clear_source_cards(session, table_id, sources, *, scope=None, edit_session_id=None):
    """Clear exactly the reviewed complete sources; retain evidence and history."""
    from document_pipeline_api.services.read_snapshot import begin_write_snapshot
    from document_pipeline_api.services.data_tables import table_columns, delete_rows
    begin_write_snapshot(session)
    selection = prepare_export_selection(session, table_id, scope)
    expected = {parse_source_key(source['id']): source['version'] for source in sources}
    if not expected or len(expected) != len(sources):
        raise HTTPException(422, '请选择不同的文件或来源。')
    cards = _headers(session, table_id, list(expected), table_columns(session, selection.table))
    if set(cards) != set(expected) or any(cards[key]['version'] != version for key, version in expected.items()):
        raise HTTPException(409, '选中的数据已经变化，请重新查看后再清空。')
    identity = source_identity_expression()
    matches = dict(session.execute(select(identity, func.count()).where(
        *selection.predicates, identity.in_(expected)).group_by(identity)).all())
    if any(matches.get(key, 0) != card['row_count'] for key, card in cards.items()):
        raise HTTPException(409, '当前范围只包含部分明细，不能清空整个来源；请选择明细删除。')
    identifiers = list(session.scalars(select(DataRowRecord.id).where(
        *selection.predicates, identity.in_(expected))))
    count = delete_rows(session, table_id, identifiers, edit_session_id=edit_session_id, commit=False)
    session.commit()
    return {'deleted': count, 'sources': len(expected)}
