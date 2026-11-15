"""Exercise frozen Windows current-data and revision lifecycle on a disposable copy."""
import argparse
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--executable', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--port', type=int, default=18825)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / '.local'
    source, output, executable = args.source.resolve(), args.output.resolve(), args.executable.resolve()
    if not source.is_relative_to(root) or not output.is_relative_to(root) or output.exists():
        raise SystemExit('Use a fresh .local output and an isolated UI fixture source.')
    ids = json.loads((source / 'seeded.json').read_text(encoding='utf-8'))
    if len(ids) < 2 or not executable.is_file():
        raise SystemExit('Need the synthetic fixture and a built executable.')
    output.mkdir(parents=True)
    database = output / 'document-pipeline.db'
    with sqlite3.connect((source / 'data.db').as_uri() + '?mode=ro', uri=True) as before:
        with sqlite3.connect(database) as after:
            before.backup(after)
    shutil.copytree(source / 'uploads', output / 'uploads')
    checks = {}
    process = None
    log = (output / 'process.log').open('ab')
    client = httpx.Client(base_url=f'http://127.0.0.1:{args.port}/api/v1', timeout=20, trust_env=False)

    def request(method, url, **kwargs):
        response = client.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None

    def sql(statement, params=()):
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as connection:
            return connection.execute(statement, params).fetchall()

    def start():
        nonlocal process
        process = subprocess.Popen([str(executable), 'start', '--data-dir', str(output), '--port', str(args.port), '--no-browser'],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('Frozen supervisor exited; see process.log')
            try:
                health = request('GET', '/health')
                state = json.loads((output / 'runtime/supervisor.json').read_text(encoding='utf-8'))
                if health['status'] == 'ok' and state['pid'] == process.pid:
                    assert health['version'] == '0.5.0', health
                    return
            except (httpx.HTTPError, OSError, ValueError, KeyError):
                pass
            time.sleep(.2)
        raise RuntimeError('Frozen supervisor startup timed out')

    def stop():
        subprocess.run([str(executable), 'stop', '--data-dir', str(output)], check=True,
            stdout=log, stderr=log, timeout=25, creationflags=subprocess.CREATE_NO_WINDOW)
        process.wait(timeout=25)

    def edit(file_id, value, session):
        current = request('GET', f'/tasks/{file_id}/result')
        request('PATCH', f'/tasks/{file_id}/current', json={'expected_version': current['review_version'],
            'header_changes': {'f1': value}, 'edit_session_id': session})
        assert request('GET', f'/tasks/{file_id}/result')['result']['header']['f1'] == value
        assert json.loads(sql('SELECT header_json FROM file_current_data WHERE task_id=?', (file_id,))[0][0])['f1'] == value

    try:
        start()
        initial = {task: request('GET', f'/tasks/{task}/revisions')['total'] for task in ids[:2]}
        session = request('POST', '/tables/edit-sessions')['id']
        for task, count in zip(ids[:2], (5, 3)):
            for index in range(count):
                edit(task, f'Frozen session {task[-4:]} edit {index + 1}', session)
            history = request('GET', f'/tasks/{task}/revisions')
            assert history['total'] == initial[task] + 1 and not history['revisions'][0]['sealed']
        checks['immediate_durable_writes_and_one_version_per_file'] = True
        stop()
        checks['normal_close_sealed_before_restart'] = sql('SELECT COUNT(*) FROM file_revisions WHERE sealed_at IS NULL')[0][0] == 0
        assert checks['normal_close_sealed_before_restart'], 'Normal close left open revisions before restart'
        start()
        session = request('POST', '/tables/edit-sessions')['id']
        edit(ids[0], 'Durable before forced process exit', session)
        before_crash = request('GET', f'/tasks/{ids[0]}/revisions')['total']
        process.kill()  # Only this script's own Popen handle; Job Object owns its children.
        process.wait(timeout=15)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                client.get('/health', timeout=.5)
            except httpx.HTTPError:
                break
            time.sleep(.1)
        else:
            raise RuntimeError('Owned children survived supervisor crash')
        assert sql('SELECT COUNT(*) FROM file_revisions WHERE sealed_at IS NULL')[0][0] == 1
        start()
        history = request('GET', f'/tasks/{ids[0]}/revisions')
        assert history['total'] == before_crash and history['revisions'][0]['sealed']
        assert request('GET', f'/tasks/{ids[0]}/result')['result']['header']['f1'] == 'Durable before forced process exit'
        checks['forced_exit_recovers_without_data_loss_or_extra_version'] = True
        current = request('GET', f'/tasks/{ids[0]}/result')
        deleted_id, deleted_value = current['detail_ids'][0], current['result']['items'][0]
        request('PATCH', f'/tasks/{ids[0]}/current', json={'expected_version': current['review_version'], 'deleted_ids': [deleted_id]})
        after = request('GET', f'/tasks/{ids[0]}/result')
        assert deleted_id not in after['detail_ids']
        revision = request('GET', f'/tasks/{ids[0]}/revisions')['revisions'][0]
        details = request('GET', f'/tasks/{ids[0]}/revisions/{revision["id"]}')
        assert details['items'][0]['before']['values'] == deleted_value
        request('PATCH', f'/tasks/{ids[0]}/current', json={'expected_version': after['review_version'], 'clear': True})
        assert not request('GET', f'/tasks/{ids[0]}/result')['has_current_data']
        assert client.get(f'/tasks/{ids[0]}/file').status_code == 200
        checks['deleting_current_data_preserves_original_and_complete_history'] = True
        original_path = sql('SELECT storage_path FROM tasks WHERE id=?', (ids[0],))[0][0]
        request('DELETE', f'/tasks/{ids[0]}')
        assert client.get(f'/tasks/{ids[0]}/result').status_code == 404
        for table in ('data_rows', 'file_current_data', 'file_revisions'):
            assert sql(f'SELECT COUNT(*) FROM {table} WHERE task_id=?', (ids[0],))[0][0] == 0
        assert not (output / 'uploads' / original_path).exists()
        assert request('GET', f'/tasks/{ids[1]}/result')['has_current_data']
        checks['whole_file_deletion_has_no_current_or_history_or_original_orphans'] = True
        stop()
    finally:
        if process and process.poll() is None:
            stop()
        client.close()
        log.close()
        (output / 'report.json').write_text(json.dumps({'checks': checks}, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(checks, ensure_ascii=False))


if __name__ == '__main__':
    main()
