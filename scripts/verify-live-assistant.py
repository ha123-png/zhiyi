"""Real cloud assistant acceptance on the synthetic live-queue fixture only."""
from argparse import ArgumentParser
import json
from pathlib import Path
import time
from urllib.parse import urlparse
from uuid import uuid4

import httpx


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    allowed = Path(__file__).resolve().parents[1] / '.local'
    output, fixture_path = args.output.resolve(), args.fixture.resolve()
    if not output.is_relative_to(allowed) or not fixture_path.is_relative_to(allowed) or output.exists():
        parser.error('Use local synthetic fixtures and a new report path')
    if urlparse(args.url).hostname not in ('127.0.0.1', 'localhost'):
        parser.error('Acceptance must target localhost')
    fixture = json.loads(fixture_path.read_text(encoding='utf-8'))
    assert fixture['passed'] and fixture['synthetic']
    task_id = fixture['task_id']
    report = {'passed': False, 'synthetic': True, 'checks': {}, 'questions': []}

    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    save()
    with httpx.Client(base_url=args.url + '/api/v1', trust_env=False, timeout=180) as client:
        def request(method, url, **kwargs):
            response = client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()

        profile = next(p for p in request('GET', '/models/profiles') if p['id'] == fixture['profile_id'])
        assert profile['is_remote'], 'This workflow is cloud-only'
        context = {'task_id': task_id, 'mode': 'assist'}
        current_url = f'/tasks/{task_id}/current-details'
        before = request('GET', current_url)
        assert before['header']['order_id'] == 'LIVE-20260927-001'
        customer = f'松林云端核对-{uuid4().hex[:6]}'
        edit_question = f'请把这份文件的客户修改为“{customer}”，其他内容保持原值，生成一次修改预览。'

        def ask(question):
            entry = {'text': question, 'request_id': str(uuid4()), 'dispatched': True}
            report['questions'].append(entry)
            save()
            started = time.monotonic()
            ids = request('POST', '/assistant/runs', json={
                'text': question, 'request_id': entry['request_id'],
                'profile_id': profile['id'], 'profile_version': profile['version'],
                'remote_consent': f"{profile['id']}:{profile['version']}", 'context': context,
            })
            entry.update(ids)
            save()
            with client.stream('GET', f"/assistant/runs/{ids['run_id']}/events") as stream:
                stream.raise_for_status()
                for _ in stream.iter_lines():
                    pass
            thread = request('GET', f"/assistant/threads/{ids['thread_id']}")
            entry.update(thread=thread, seconds=round(time.monotonic() - started, 2))
            save()
            assert thread['runs'][0]['status'] == 'completed', thread['runs'][0]['status']
            return thread

        analysis = ask('这份采购单的金额合计是多少？请调用系统统计工具核对当前数据。')
        totals = [tool.get('result', {}).get('totals', {}) for tool in analysis['tools']]
        report['checks']['deterministic_total_39'] = any(39 in total.values() for total in totals)
        proposal = ask(edit_question)
        pending = [tool for tool in proposal['tools'] if tool['status'] == 'pending']
        assert len(pending) == 1, 'Expected one explicit pending change preview'
        tool_id = pending[0]['id']
        assert request('GET', current_url) == before
        auth_url = f"/assistant/threads/{proposal['id']}/authorization"
        auth = {'context': {**context, 'mode': 'read'}, 'profile_id': profile['id'],
                'profile_version': profile['version'], 'remote_authorized': True}
        changed = request('PUT', auth_url, json=auth)
        report['checks']['old_plan_invalidated'] = changed['invalidated_tools'] == 1
        denied = client.post(f'/assistant/tools/{tool_id}/decision', json={'approve': True})
        report['rejected_plan_response'] = {'http_status': denied.status_code, 'status': denied.json().get('status')}
        report['checks']['read_mode_rejects_old_plan'] = denied.status_code == 200 and denied.json()['status'] == 'stale' and request('GET', current_url) == before
        request('PUT', auth_url, json={**auth, 'context': context})
        denied = client.post(f'/assistant/tools/{tool_id}/decision', json={'approve': True})
        report['checks']['regrant_does_not_revive_old_plan'] = denied.status_code == 200 and denied.json()['status'] == 'stale' and request('GET', current_url) == before
        save()
        fresh = ask(edit_question)
        pending = [tool for tool in fresh['tools'] if tool['status'] == 'pending']
        assert len(pending) == 1
        approved = request('POST', f"/assistant/tools/{pending[0]['id']}/decision", json={'approve': True})
        after = request('GET', current_url)
        report['checks']['fresh_approved_edit'] = approved['status'] == 'approved' and after['header'] == {**before['header'], 'customer': customer}
        report['checks']['details_preserved'] = [
            {key: value for key, value in row.items() if key not in ('version', 'updated_at')}
            for row in after['items']
        ] == [
            {key: value for key, value in row.items() if key not in ('version', 'updated_at')}
            for row in before['items']
        ]
        report['checks']['row_versions_advance'] = all(
            new['version'] != old['version'] for old, new in zip(before['items'], after['items'], strict=True)
        )
        history = ask('这份文件最近一次修订改变了什么？请查文件修订历史，告诉我修改前后的客户名称。')
        report['checks']['history_tool_used'] = any(tool.get('result', {}).get('kind') in ('file_revisions', 'file_revision') for tool in history['tools'])
        report['passed'] = all(report['checks'].values())
        save()
        print(json.dumps({'passed': report['passed'], 'checks': report['checks']}, ensure_ascii=False))
        if not report['passed']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
