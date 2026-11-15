"""Real-provider autonomous extraction and on-demand history/handbook acceptance.

Requires the isolated scale server; never opens installed user data. Results are
synthetic. A unique output file prevents accidentally repeating paid requests.
"""
from argparse import ArgumentParser
import json
from pathlib import Path
import time

import httpx
from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.db import build_engine
from document_pipeline_api.model_providers.openai_compatible import OpenAICompatibleProvider
from document_pipeline_api.models import DataTableRecord
from document_pipeline_api.services.extraction import process_task
from verify_assistant_isolation import require_bound_server, reserve_report, validate_loopback_url, validate_paths


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--url', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--key-file', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    root, output = validate_paths(project, args.data_dir, args.output)
    base_url = validate_loopback_url(args.url)
    reserve_report(output)
    settings = Settings(database_url=f'sqlite:///{root / "document-pipeline.db"}', storage_dir=root/'uploads', queue_enabled=False)
    engine = build_engine(settings.database_url)
    with Session(engine) as session:
        assert session.get(DataTableRecord, 'ask-synthetic'), 'Synthetic marker missing'
    report = {'model': args.model, 'checks': {}, 'questions': []}
    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    save()
    with httpx.Client(base_url=base_url+'/api/v1', timeout=300, trust_env=False, follow_redirects=False) as client:
        require_bound_server(engine, client)
        provider = OpenAICompatibleProvider(
            'https://dashscope.aliyuncs.com/compatible-mode/v1' if args.key_file else 'http://127.0.0.1:1234/v1',
            args.model, api_key=args.key_file.read_text(encoding='utf-8-sig').strip() if args.key_file else '',
            temperature=0.1, reasoning_effort='none', timeout_seconds=180)
        def ok(response):
            response.raise_for_status()
            return response.json()
        profile = next(p for p in ok(client.get('/models/profiles')) if p['model_name'] == args.model)
        template = ok(client.get('/templates/builtin-autonomous'))
        report['checks']['fallback_off_initially'] = not template['in_smart_pool']
        # Changes are isolated settings; fixed fields remain absent.
        template = ok(client.put('/templates/builtin-autonomous', json={
            **{key: template[key] for key in ('name', 'description', 'fields', 'validation_rules', 'deterministic_rules', 'output_mapping', 'behavior')},
            'expected_version': template['version'],
            'extra_instructions': '关注项目、客户、日期和各项名称、数量、单价、金额；数字按原文提取。'}))
        content = '合成测试：秋季书展布置\n客户：合成文化中心\n日期：2026-09-21\n项目：秋季书展布置\n明细：\n展台，数量3，单价120元，金额360元。\n标牌，数量5，单价20元，金额100元。\n以上均为虚构验收数据。'
        task = ok(client.post('/tasks', files={'file': ('自主提取验收.txt', content.encode(), 'text/plain')},
            data={'template_id': 'builtin-autonomous', 'model_profile_id': profile['id'],
                  'model_profile_version': str(profile['version'])}))
        report['task_id'] = task['id']
        report['extraction_dispatched'] = True
        save()
        started = time.monotonic()
        with Session(engine) as session:
            process_task(session, settings, task['id'], client=provider)
        result = ok(client.get(f'/tasks/{task["id"]}/result'))
        report['extraction_seconds'] = round(time.monotonic()-started, 2)
        report['extraction'] = result
        values = result['result']
        report['checks']['autonomous_schema'] = bool(result['current_schema']) and not template['fields']
        report['checks']['header_facts'] = {'秋季书展布置', '合成文化中心', '2026-09-21'} <= set(values['header'].values())
        numeric = [{v for v in row.values() if type(v) in (int, float)} for row in values['items']]
        report['checks']['two_exact_details'] = len(numeric) == 2 and any({3, 120, 360} <= row for row in numeric) and any({5, 20, 100} <= row for row in numeric)
        save()
        date_key = next(key for key, value in values['header'].items() if value == '2026-09-21')
        current = ok(client.get(f'/tasks/{task["id"]}/current-details'))
        ok(client.patch(f'/tasks/{task["id"]}/current', json={'expected_version': current['version'], 'header_changes': {date_key: '2026-09-22'}}))
        def ask(question):
            entry = {'question': question, 'dispatched': True}
            report['questions'].append(entry)
            save()
            ids = ok(client.post('/assistant/runs', json={'text': question, 'profile_id': profile['id'],
                'profile_version': profile['version'], 'remote_consent': f'{profile["id"]}:{profile["version"]}' if profile['is_remote'] else None,
                'context': {'task_id': task['id'], 'mode': 'read'}}))
            with client.stream('GET', f'/assistant/runs/{ids["run_id"]}/events') as stream:
                stream.raise_for_status()
                for _ in stream.iter_lines():
                    pass
            thread = ok(client.get(f'/assistant/threads/{ids["thread_id"]}'))
            run = next(item for item in thread['runs'] if item['id'] == ids['run_id'])
            message = next(item for item in thread['messages'] if item['id'] == run['message_id'])
            tool_ids = {part['id'] for part in message['parts'] if part['type'] == 'tool'}
            entry.update(run=run, tools=[tool for tool in thread['tools'] if tool['id'] in tool_ids],
                answer='\n'.join(part['text'] for part in message['parts'] if part['type'] == 'text'))
            save()
            return entry
        history = ask('这份文件最新一轮修订改了什么？请查修订历史，说明修改前后的值。')
        report['checks']['reads_real_file_history'] = history['run']['status'] == 'completed' and any(t.get('result', {}).get('kind') in ('file_revisions', 'file_revision') for t in history['tools']) and '2026-09-21' in history['answer'] and '2026-09-22' in history['answer']
        manual = ask('请按知意的使用手册解释：在数据仓库删除这份文件的最后一条明细，原文件和修订历史会不会也消失？')
        report['checks']['handbook_on_demand'] = manual['run']['status'] == 'completed' and any(t.get('name') == 'read_handbook' for t in manual['tools'])
        save()
        print(json.dumps({'model': args.model, 'checks': report['checks']}, ensure_ascii=False), flush=True)
        if not all(report['checks'].values()):
            raise SystemExit(1)


if __name__ == '__main__':
    main()
