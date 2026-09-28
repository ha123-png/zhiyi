"""Read adapter for pre-0.5 templates with a shared header/item key.

Only field identity is normalized. Stored template versions and extraction
evidence remain unchanged, and rule paths retain their original section.
"""
from copy import deepcopy


def normalize_fields(fields: list[dict]) -> tuple[list[dict], dict[str, str]]:
    header = {field['key'] for field in fields if field.get('section', 'header') == 'header'}
    occupied = {field['key'] for field in fields}
    aliases = {}
    for field in fields:
        key = field['key']
        if field.get('section') == 'item' and key in header and key not in aliases:
            base = ('item_' + key)[:110]
            candidate, index = base, 2
            while candidate in occupied:
                candidate, index = f'{base}_{index}', index + 1
            aliases[key] = candidate
            occupied.add(candidate)
    normalized = [{**field, 'key': aliases.get(field['key'], field['key'])
                   if field.get('section') == 'item' else field['key']} for field in fields]
    return normalized, aliases


def normalize_paths(value, aliases):
    if isinstance(value, dict):
        return {key: normalize_paths(child, aliases) for key, child in value.items()}
    if isinstance(value, list):
        return [normalize_paths(child, aliases) for child in value]
    if isinstance(value, str):
        for prefix in ('items[].', 'item.'):
            if value.startswith(prefix) and value[len(prefix):] in aliases:
                return prefix + aliases[value[len(prefix):]]
    return value


def normalize_template_payload(payload: dict) -> dict:
    result = deepcopy(payload)
    fields, aliases = normalize_fields(result['fields'])
    result['fields'] = fields
    for key in ('deterministic_rules', 'behavior'):
        if key in result:
            result[key] = normalize_paths(result[key], aliases)
    for old, new in aliases.items():
        if old in result.get('output_mapping', {}):
            result['output_mapping'][new] = result['output_mapping'][old]
    return result
