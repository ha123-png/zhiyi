"""Exercise the real HTTP upload and worker path with synthetic facts.

Uses an already running isolated acceptance instance and an explicit provider.
Cloud secrets go only through the product's protected credential store, never
into reports. A durable report refuses accidental repeated inference.
"""
from argparse import ArgumentParser
from hashlib import sha256
import json
from pathlib import Path
import time
from urllib.parse import urlparse

import httpx


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--provider', choices=['cloud', 'local'], default='cloud')
    parser.add_argument('--key-file', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if not output.is_relative_to(project / '.local') or output.exists():
        parser.error('Use a new report under this workspace .local directory')
    if urlparse(args.url).hostname not in ('127.0.0.1', 'localhost'):
        parser.error('Only isolated localhost acceptance instances are supported')
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {'passed': False, 'real_model': args.model, 'synthetic': True, 'checks': {}}

    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    save()
    with httpx.Client(base_url=args.url + '/api/v1', trust_env=False, timeout=30) as client:
        def request(method, url, **kwargs):
            response = client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()

        # Verify the explicit synthetic marker before creating anything.
        templates = request('GET', '/templates')
        template = next(t for t in templates if t['name'] == '采购记录' and '合成样本' in t['description'])
        profiles = request('GET', '/models/profiles')
        profile = next((p for p in profiles if p['model_name'] == args.model and not p['is_archived']), None)
        if profile is None:
            cloud = args.provider == 'cloud'
            if cloud and not args.key_file:
                parser.error('Cloud inference requires an explicitly supplied key file')
            body = {
                'name': '云端验收' if cloud else 'LM Studio · 本地验收',
                'provider': 'openai_compatible' if cloud else 'lm_studio',
                'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1' if cloud else 'http://127.0.0.1:1234/v1',
                'model_name': args.model, 'context_length': 32768 if cloud else 8192,
                'timeout_seconds': 180, 'temperature': 0,
                'reasoning_effort': 'none', 'multimodal': True,
                'acknowledge_remote_data_transfer': cloud,
            }
            if cloud:
                body['api_key'] = args.key_file.read_text(encoding='utf-8-sig').strip()
            profile = request('POST', '/models/profiles', json=body)
            body.pop('api_key', None)
        request('POST', f"/models/profiles/{profile['id']}/activate", json={
            'version': profile['version'], 'acknowledge_remote_data_transfer': args.provider == 'cloud',
        })
        raw = ('合成采购单（无真实业务数据）\n客户：松林测试工作室\n采购日期：2026-09-27\n'
               '单号：LIVE-20260927-001\n项目、数量、单价、金额：\n'
               '纸袋，2，12，24\n签字笔，3，5，15\n').encode()
        task = request('POST', '/tasks', files={'file': ('真实队列合成采购单.txt', raw, 'text/plain')},
                       data={'template_id': template['id'], 'model_profile_id': profile['id'],
                             'model_profile_version': str(profile['version'])})
        report.update(task_id=task['id'], profile_id=profile['id'], dispatched=True)
        save()
        started = time.monotonic()
        statuses = []
        while time.monotonic() - started < 360:
            tasks = request('GET', '/tasks', params={'limit': 100})
            status = next(item for item in tasks if item['id'] == task['id'])
            if not statuses or statuses[-1] != status['status']:
                statuses.append(status['status'])
                report['statuses'] = statuses
                save()
            if status['status'] in ('completed', 'failed', 'cancelled'):
                break
            time.sleep(1)
        assert status['status'] == 'completed', f"Worker ended in {status['status']}"
        current = request('GET', f"/tasks/{task['id']}/current-details")
        report['seconds'] = round(time.monotonic() - started, 2)
        report['current'] = current
        report['checks']['header_exact'] = current['header'] == {
            'customer': '松林测试工作室', 'date': '2026-09-27', 'order_id': 'LIVE-20260927-001',
        }
        report['checks']['two_details'] = current['total'] == 2
        report['checks']['detail_exact'] = [row['values'] for row in current['items']] == [
            {'item': '纸袋', 'quantity': 2, 'unit_price': 12, 'amount': 24},
            {'item': '签字笔', 'quantity': 3, 'unit_price': 5, 'amount': 15},
        ]
        original = client.get(f"/tasks/{task['id']}/file")
        report['checks']['original_intact'] = original.is_success and sha256(original.content).digest() == sha256(raw).digest()
        report['passed'] = all(report['checks'].values())
        save()
        print(json.dumps({key: report[key] for key in ('passed', 'checks', 'seconds', 'statuses')}, ensure_ascii=False))
        if not report['passed']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
