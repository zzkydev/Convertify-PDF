
## app.py (Backend Flask)

import os
import uuid
import shutil
import zipfile
import tempfile
import subprocess
from typing import List

from flask import Flask, request, send_file, jsonify
from werkzeug.utils import secure_filename
from pdf2docx import Converter
from pypdf import PdfWriter, PdfReader
from PIL import Image
from pdf2image import convert_from_path

# ===== Konfigurasi dasar =====
BASE_DIR = os.path.dirname(__file__)
UPLOAD_DIR = os.path.join(BASE_DIR, 'tmp_uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_PDF = {'.pdf'}
ALLOWED_IMG = {'.png', '.jpg', '.jpeg'}
MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100 MB

app = Flask(__name__, static_url_path='/static', static_folder='static')
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH


def allowed(filename: str, allowed_exts: set) -> bool:
    _, ext = os.path.splitext(filename.lower())
    return ext in allowed_exts


@app.get('/')
def index():
    return app.send_static_file('index.html')


# ===== 1) PDF -> DOCX =====
@app.post('/api/convert/pdf-to-docx')
def pdf_to_docx():
    if 'file' not in request.files:
        return jsonify({'error': 'Tidak ada file pada key "file"'}), 400
    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'Nama file kosong'}), 400
    if not allowed(f.filename, ALLOWED_PDF):
        return jsonify({'error': 'Harap unggah PDF'}), 400

    uid = uuid.uuid4().hex
    safe_name = secure_filename(f.filename)
    pdf_path = os.path.join(UPLOAD_DIR, f'{uid}_{safe_name}')
    f.save(pdf_path)

    base, _ = os.path.splitext(safe_name)
    docx_name = f"{base or 'converted'}_{uid}.docx"
    docx_path = os.path.join(UPLOAD_DIR, docx_name)

    try:
        cv = Converter(pdf_path)
        cv.convert(docx_path)
        cv.close()
    except Exception as e:
        try: os.remove(pdf_path)
        except: pass
        return jsonify({'error': f'Gagal konversi: {e}'}), 500
    finally:
        try: os.remove(pdf_path)
        except: pass

    return send_file(docx_path, as_attachment=True, download_name=docx_name,
                     mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document')


# ===== 2) OCR PDF (ocrmypdf) -> PDF searchable =====
# Membutuhkan dependency sistem: Tesseract + Ghostscript + poppler (untuk pdf2image) di OS.
@app.post('/api/ocr/pdf')
def ocr_pdf():
    if 'file' not in request.files:
        return jsonify({'error': 'Tidak ada file pada key "file"'}), 400
    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'Nama file kosong'}), 400
    if not allowed(f.filename, ALLOWED_PDF):
        return jsonify({'error': 'Harap unggah PDF'}), 400

    uid = uuid.uuid4().hex
    safe_name = secure_filename(f.filename)
    src_pdf = os.path.join(UPLOAD_DIR, f'{uid}_{safe_name}')
    out_pdf = os.path.join(UPLOAD_DIR, f"ocr_{uid}_{safe_name}")
    f.save(src_pdf)

    lang = request.form.get('lang', 'eng')  # contoh: 'eng+ind'

    try:
        # Jalankan ocrmypdf via subprocess agar kompatibel lintas OS
        # --optimize 1 agar lebih cepat, --skip-text untuk file sudah searchable
        cmd = ['ocrmypdf', '--skip-text', '--optimize', '1', '-l', lang, src_pdf, out_pdf]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    except Exception as e:
        try: os.remove(src_pdf)
        except: pass
        return jsonify({'error': f'OCR gagal: {e}'}), 500
    finally:
        try: os.remove(src_pdf)
        except: pass

    return send_file(out_pdf, as_attachment=True, download_name=os.path.basename(out_pdf), mimetype='application/pdf')


# ===== 3) Merge PDF (gabungkan) =====
@app.post('/api/merge/pdf')
def merge_pdf():
    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': 'Tidak ada file PDF yang dikirim (key "files")'}), 400

    writer = PdfWriter()
    tmp_paths: List[str] = []
    uid = uuid.uuid4().hex

    try:
        for f in files:
            if not f or not f.filename: continue
            if not allowed(f.filename, ALLOWED_PDF):
                return jsonify({'error': f'Format tidak didukung: {f.filename}'}), 400
            safe = secure_filename(f.filename)
            p = os.path.join(UPLOAD_DIR, f'{uid}_{safe}')
            f.save(p)
            tmp_paths.append(p)
            reader = PdfReader(p)
            for page in reader.pages:
                writer.add_page(page)

        out_path = os.path.join(UPLOAD_DIR, f'merged_{uid}.pdf')
        with open(out_path, 'wb') as fp:
            writer.write(fp)
    except Exception as e:
        return jsonify({'error': f'Gagal menggabungkan: {e}'}), 500
    finally:
        for p in tmp_paths:
            try: os.remove(p)
            except: pass

    return send_file(out_path, as_attachment=True, download_name=f'merged_{uid}.pdf', mimetype='application/pdf')


# ===== 4) PNG/JPG -> PDF (satu atau banyak gambar jadi satu PDF) =====
@app.post('/api/convert/img-to-pdf')
def img_to_pdf():
    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': 'Tidak ada file (key "files")'}), 400

    images: List[Image.Image] = []
    tmp_paths: List[str] = []
    uid = uuid.uuid4().hex
    try:
        for f in files:
            if not f or not f.filename: continue
            if not allowed(f.filename, ALLOWED_IMG):
                return jsonify({'error': f'Format tidak didukung: {f.filename}'}), 400
            safe = secure_filename(f.filename)
            p = os.path.join(UPLOAD_DIR, f'{uid}_{safe}')
            f.save(p)
            tmp_paths.append(p)
            im = Image.open(p)
            if im.mode in ("RGBA", "LA"):
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            else:
                im = im.convert('RGB')
            images.append(im)

        if not images:
            return jsonify({'error': 'Tidak ada gambar valid'}), 400

        out_path = os.path.join(UPLOAD_DIR, f'images_{uid}.pdf')
        first, *rest = images
        first.save(out_path, save_all=True, append_images=rest)
    except Exception as e:
        return jsonify({'error': f'Gagal konversi: {e}'}), 500
    finally:
        for p in tmp_paths:
            try: os.remove(p)
            except: pass
        for im in images:
            try: im.close()
            except: pass

    return send_file(out_path, as_attachment=True, download_name=os.path.basename(out_path), mimetype='application/pdf')


# ===== 5) PDF -> PNG (satu PNG per halaman, dikemas ZIP) =====
@app.post('/api/convert/pdf-to-png')
def pdf_to_png():
    if 'file' not in request.files:
        return jsonify({'error': 'Tidak ada file pada key "file"'}), 400
    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'Nama file kosong'}), 400
    if not allowed(f.filename, ALLOWED_PDF):
        return jsonify({'error': 'Harap unggah PDF'}), 400

    uid = uuid.uuid4().hex
    safe = secure_filename(f.filename)
    pdf_path = os.path.join(UPLOAD_DIR, f'{uid}_{safe}')
    f.save(pdf_path)

    dpi = int(request.form.get('dpi', 200))

    tmp_dir = tempfile.mkdtemp(prefix=f'pdf2png_{uid}_', dir=UPLOAD_DIR)
    out_zip = os.path.join(UPLOAD_DIR, f'pages_{uid}.zip')

    try:
        # membutuhkan Poppler (pastikan sudah terpasang di OS dan PATH)
        images = convert_from_path(pdf_path, dpi=dpi)
        png_paths = []
        for i, img in enumerate(images, start=1):
            p = os.path.join(tmp_dir, f'page_{i:03d}.png')
            img.save(p, 'PNG')
            png_paths.append(p)

        with zipfile.ZipFile(out_zip, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            for p in png_paths:
                zf.write(p, arcname=os.path.basename(p))
    except Exception as e:
        return jsonify({'error': f'Gagal PDF ke PNG: {e}'}), 500
    finally:
        try: os.remove(pdf_path)
        except: pass
        # bersihkan folder tmp_dir setelah ZIP dikirim? (biarkan untuk saat ini)

    return send_file(out_zip, as_attachment=True, download_name=os.path.basename(out_zip), mimetype='application/zip')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
# ```python ```
# import os
# import uuid
# from flask import Flask, request, send_file, jsonify
# from pdf2docx import Converter
# from werkzeug.utils import secure_filename

# # Konfigurasi dasar
# UPLOAD_DIR = os.path.join(os.path.dirname(__file__), 'tmp_uploads')
# os.makedirs(UPLOAD_DIR, exist_ok=True)

# ALLOWED_EXTENSIONS = {'.pdf'}
# MAX_CONTENT_LENGTH = 25 * 1024 * 1024  # 25 MB

# app = Flask(__name__, static_url_path='/static', static_folder='static')
# app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH


# def allowed_file(filename: str) -> bool:
#     _, ext = os.path.splitext(filename.lower())
#     return ext in ALLOWED_EXTENSIONS


# @app.get('/')
# def index():
#     # Halaman statis
#     return app.send_static_file('index.html')


# @app.post('/api/convert')
# def convert_pdf_to_docx():
#     if 'file' not in request.files:
#         return jsonify({'error': 'Tidak ada file pada form-data key "file"'}), 400

#     f = request.files['file']
#     if f.filename == '':
#         return jsonify({'error': 'Nama file kosong'}), 400

#     if not allowed_file(f.filename):
#         return jsonify({'error': 'Format tidak didukung. Harap unggah PDF.'}), 400

#     # Simpan PDF sementara
#     safe_name = secure_filename(f.filename)
#     uid = uuid.uuid4().hex
#     pdf_path = os.path.join(UPLOAD_DIR, f"{uid}_{safe_name}")
#     f.save(pdf_path)

#     # Siapkan nama DOCX
#     base, _ = os.path.splitext(safe_name)
#     docx_name = f"{base or 'converted'}_{uid}.docx"
#     docx_path = os.path.join(UPLOAD_DIR, docx_name)

#     try:
#         # Konversi dengan pdf2docx
#         cv = Converter(pdf_path)
#         cv.convert(docx_path)  # dapat ditambah pages=(start,end)
#         cv.close()
#     except Exception as e:
#         # Hapus PDF sementara jika gagal
#         try:
#             os.remove(pdf_path)
#         except Exception:
#             pass
#         return jsonify({'error': f'Gagal konversi: {str(e)}'}), 500

#     # Kirim file hasil konversi dan bersihkan file PDF (opsional)
#     try:
#         # Hapus PDF input agar folder bersih; DOCX dibiarkan untuk dikirim
#         os.remove(pdf_path)
#     except Exception:
#         pass

#     # Kirim sebagai attachment agar langsung terunduh di browser
#     return send_file(
#         docx_path,
#         as_attachment=True,
#         download_name=docx_name,
#         mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
#     )


# if __name__ == '__main__':
#     # Jalankan server
#     app.run(host='0.0.0.0', port=5000, debug=True)