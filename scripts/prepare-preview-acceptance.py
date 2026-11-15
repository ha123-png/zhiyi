import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4
import zipfile
import zlib

sys.path.insert(0, str(Path('apps/api/src').resolve()))
from fastapi.testclient import TestClient
from openpyxl import Workbook
from PIL import Image, ImageDraw
from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import TaskRecord, DataTableRecord
from document_pipeline_api.services.current_data import initialize_current

from argparse import ArgumentParser
parser = ArgumentParser(description='Create synthetic bounded-preview fixtures in a new isolated workspace.')
parser.add_argument('--root', type=Path, required=True)
args = parser.parse_args()
ROOT = args.root.resolve()
if not ROOT.is_relative_to(Path(__file__).resolve().parents[1] / '.local'):
    parser.error('Preview fixtures must stay under this workspace .local directory')
ROOT.mkdir(parents=True, exist_ok=False)
FIXTURES = ROOT / 'fixtures'
DATA = ROOT / 'data'
FIXTURES.mkdir()
DATA.mkdir()


def pdf(pages):
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'', b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    kids = []
    for page in range(1, pages + 1):
        page_id = len(objects) + 1
        kids.append(f'{page_id} 0 R')
        landscape = page % 10 == 0
        width, height = (842, 595) if landscape else (595, 842)
        objects.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] /Resources << /Font << /F1 3 0 R >> >> /Contents {page_id + 1} 0 R >>'.encode())
        lines = [f'BT /F1 24 Tf 40 {height - 70} Td (SYNTHETIC ORIGINAL - PAGE {page} / {pages}) Tj ET',
                 f'BT /F1 12 Tf 40 {height - 100} Td (Read-only preview test. No real business data.) Tj ET']
        for n in range(15):
            y = height - 150 - n * 24
            lines.append(f'BT /F1 12 Tf 40 {y} Td (Source row {page}-{n + 1:02d}   Quantity {n + 1}   Amount {(n + 1) * 28}.00) Tj ET')
        stream = zlib.compress('\n'.join(lines).encode())
        objects.append(f'<< /Length {len(stream)} /Filter /FlateDecode >>\nstream\n'.encode() + stream + b'\nendstream')
    objects[1] = f'<< /Type /Pages /Count {pages} /Kids [{" ".join(kids)}] >>'.encode()
    output = bytearray(b'%PDF-1.7\n')
    offsets = [0]
    for index, item in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f'{index} 0 obj\n'.encode() + item + b'\nendobj\n')
    xref = len(output)
    output.extend(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]:
        output.extend(f'{offset:010d} 00000 n \n'.encode())
    output.extend(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    return bytes(output)


for pages in (10, 100, 500):
    (FIXTURES / f'pdf-{pages}.pdf').write_bytes(pdf(pages))
book = Workbook()
book.active.title = '一万行明细'
for row in range(1, 10001):
    book.active.append([f'项目{row:05d}', row, row * 28] + [f'字段{column}-{row}' for column in range(4, 21)])
book.create_sheet('第二个工作表').append(['第二页内容', '独立Sheet'])
book.save(FIXTURES / '10000.xlsx')
with zipfile.ZipFile(FIXTURES / '10000.docx', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
    body = ''.join(f'<w:p><w:r><w:t>第{i:05d}段 合成原件内容，用于核对后续段落仍能按需读取。</w:t></w:r></w:p>' for i in range(1, 10001))
    archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + '</w:body></w:document>')
(FIXTURES / 'large.txt').write_text('\n'.join(f'第{i:05d}行 合成资料，仅用于预览。' for i in range(1, 100001)), encoding='utf-8')
picture = Image.new('RGB', (4000, 3000), '#e5e5e5')
ImageDraw.Draw(picture).text((300, 500), 'SYNTHETIC ORIGINAL IMAGE 4000 x 3000', fill='black', font_size=110)
picture.save(FIXTURES / 'photo.png')

settings = Settings(database_url=f'sqlite:///{DATA / "document-pipeline.db"}', storage_dir=DATA / 'uploads', queue_enabled=False)
settings.storage_dir.mkdir(parents=True, exist_ok=True)
schema = [dict(key='purpose', label='用途', section='header', value_type='text'), dict(key='item', label='明细', section='item', value_type='text')]
manifest = {}
with TestClient(create_app(settings)) as client:
    with client.app.state.session_factory() as session:
        table = DataTableRecord(id='preview-fixtures', name='原件预览压测', template_key='preview-test', template_version='1', document_kind='custom',
                                columns_json=json.dumps(schema), presentation_json=json.dumps({'mode':'card'}))
        session.add(table)
        session.flush()
        for path in FIXTURES.iterdir():
            suffix = path.suffix
            mime = {'.pdf':'application/pdf', '.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document', '.txt':'text/plain', '.png':'image/png'}[suffix]
            task_id = str(uuid4())
            raw = path.read_bytes()
            storage = f'{task_id}{suffix}'
            (settings.storage_dir / storage).write_bytes(raw)
            task = TaskRecord(id=task_id, filename=path.name, content_type=mime, size_bytes=len(raw), storage_path=storage,
                              sha256=hashlib.sha256(raw).hexdigest(), status='completed', page_count=int(path.stem.split('-')[1]) if suffix=='.pdf' else 1)
            session.add(task)
            session.flush()
            initialize_current(session, task_id=task_id, table_id=table.id, header={'purpose':'合成原件性能核对'},
                               items=[{'item': f'核对明细 {i}'} for i in range(1, 51)], schema=schema)
            manifest[path.name] = {'task_id':task_id, 'sha256':task.sha256, 'bytes':len(raw), 'mime':mime}
        session.commit()
(ROOT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'fixtures':len(manifest), 'data_dir':str(DATA), 'model_calls':0}))
