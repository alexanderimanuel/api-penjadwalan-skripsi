"""Inventaris sumber Agustus; bukan ekstraktor event atau validator jadwal."""
from pathlib import Path
import hashlib
import json
import re
import shutil
import pdfplumber
import pypdfium2 as pdfium
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/inventory/2026-08-03'
SOURCES = ROOT / 'data/sources/2026-08-03'
QA = ROOT / 'analysis/august/inventory'

def sha(data):
    return hashlib.sha256(data).hexdigest()

def main():
    for folder in (OUT, SOURCES, QA):
        folder.mkdir(parents=True, exist_ok=True)
    register = {'scope': 'Inventarisasi; belum validasi sel atau rekonsiliasi event', 'files': []}
    rows = ['# Register halaman sumber Agustus', '', 'Angka kata isi hanya indikator keterbacaan, bukan jumlah event.', '', '| Sumber | Halaman | Identitas sesuai PDF | Karakter teks | Kata area isi | Gambar | Perhatian |', '|---|---:|---|---:|---:|---:|---|']
    for kind, label in [('kelas', 'Kelas'), ('guru', 'Guru')]:
        source = Path('C:/Users/alexa/Downloads') / f'Jadwal KBM {label} - Berlaku 03 Agustus 2026.pdf'
        raw = source.read_bytes()
        frozen = SOURCES / source.name
        if frozen.exists() and frozen.read_bytes() != raw:
            raise ValueError(f'Salinan berbeda: {frozen}')
        if not frozen.exists():
            shutil.copyfile(source, frozen)
        record = {'id': kind, 'original_path': str(source), 'frozen_path': str(frozen.relative_to(ROOT)), 'bytes': len(raw), 'sha256': sha(raw), 'pages': []}
        with pdfplumber.open(frozen) as pdf:
            record['metadata'] = {str(k): str(v) for k, v in pdf.metadata.items()}
            record['page_count'] = len(pdf.pages)
            for number, page in enumerate(pdf.pages, 1):
                text = page.extract_text() or ''
                identity = (page.crop((0, 48, 792, 86)).extract_text() or '').strip().replace('\n', ' ')
                body = page.crop((76, 170, 642, 586)).extract_text() or ''
                flags = []
                if not body.strip():
                    flags.append('Area isi tanpa teks pelajaran; periksa visual')
                slash = sorted(set(re.findall(r'\d+\s*/\s*\d+(?:\s*/\s*\d+)*', body)))
                if slash:
                    flags.append('Beberapa kode: ' + '; '.join(slash))
                images = [{'bbox': [im['x0'], im['top'], im['x1'], im['bottom']], 'native_size': list(im['srcsize']), 'decoded_sha256': sha(im['stream'].get_data())} for im in page.images]
                item = {'page': number, 'identity_raw': identity, 'size_pt': [page.width, page.height], 'text_characters': len(text), 'body_word_count': len(body.split()), 'date_present': '03 Agustus 2026' in text, 'academic_year_present': '2026/2027' in text, 'vector_lines': len(page.lines), 'images': images, 'flags': flags}
                record['pages'].append(item)
                (QA / f'{kind}-{number:02d}.txt').write_text(text, encoding='utf-8')
                rows.append(f"| {label} | {number} | {identity} | {len(text)} | {len(body.split())} | {len(images)} | {'; '.join(flags) or '—'} |")
        identities = [p['identity_raw'] for p in record['pages']]
        record['unique_identities'] = len(set(identities))
        assert all(identities) and len(set(identities)) == len(identities)
        assert all(p['date_present'] and p['academic_year_present'] for p in record['pages'])
        assert sha(frozen.read_bytes()) == record['sha256']
        register['files'].append(record)
        doc = pdfium.PdfDocument(str(frozen))
        for start in range(0, len(doc), 9):
            sheet = Image.new('RGB', (1980, 1620), 'white')
            draw = ImageDraw.Draw(sheet)
            for offset in range(min(9, len(doc)-start)):
                page = doc[start+offset]
                bitmap = page.render(scale=0.82)
                thumb = bitmap.to_pil().convert('RGB')
                x, y = (offset % 3)*660, (offset // 3)*540
                draw.text((x+8, y+5), f'{kind} page {start+offset+1}', fill='black')
                sheet.paste(thumb, (x, y+25))
                bitmap.close()
                page.close()
            sheet.save(QA / f'{kind}-contact-{start//9+1:02d}.jpg')
        doc.close()
    (OUT / 'register.json').write_text(json.dumps(register, ensure_ascii=False, indent=2), encoding='utf-8')
    (OUT / 'register-halaman.md').write_text('\n'.join(rows)+'\n', encoding='utf-8')
    for file in register['files']:
        print(file['id'], file['page_count'], file['unique_identities'], file['sha256'])
        print('flags:', [(p['page'], p['identity_raw'], p['flags']) for p in file['pages'] if p['flags']])
        print('image hashes:', sorted(set(i['decoded_sha256'] for p in file['pages'] for i in p['images'])))

if __name__ == '__main__':
    main()
