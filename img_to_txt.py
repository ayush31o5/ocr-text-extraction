import os
import pdfplumber
import requests
import time
from flask import Flask, request, render_template, send_from_directory, url_for
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
from docx import Document

app = Flask(__name__)

UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'output'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

GEMINI_API_KEY = 'YOUR_GEMINI_API_KEY'
RETRY_MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 5
CHUNK_SIZE = 15000

def chunk_text(text, size=CHUNK_SIZE):
    sentences = text.split('.')
    chunks, chunk = [], ""
    for sentence in sentences:
        if len(chunk) + len(sentence) < size:
            chunk += sentence + '.'
        else:
            chunks.append(chunk)
            chunk = sentence + '.'
    if chunk:
        chunks.append(chunk)
    return chunks

def call_gemini_api(prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={GEMINI_API_KEY}"
    data = {"contents": [{"parts": [{"text": prompt}]}]}
    for attempt in range(RETRY_MAX_ATTEMPTS):
        try:
            response = requests.post(url, json=data)
            response.raise_for_status()
            return response.json()['candidates'][0]['content']['parts'][0]['text']
        except Exception as e:
            print(f"API error: {e}")
            time.sleep(RETRY_DELAY_SECONDS * (2 ** attempt))
    return ""

def process_pdf(pdf_path):
    all_html = ""
    with pdfplumber.open(pdf_path) as pdf:
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(process_page, page) for page in pdf.pages]
            for future in futures:
                all_html += future.result()
    return f"<html><body>{all_html}</body></html>"

def process_page(page):
    text = page.extract_text()
    if not text:
        return ""
    html = ""
    for chunk in chunk_text(text):
        prompt = f"Convert text to HTML:\n{chunk}"
        fragment = call_gemini_api(prompt)
        html += fragment or ""
    return f"<div>{html}</div>"

def html_to_docx(html_path, docx_path):
    soup = BeautifulSoup(open(html_path, 'r', encoding='utf-8'), 'html.parser')
    doc = Document()
    for element in soup.find_all(['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li']):
        text = element.get_text(separator="\n", strip=True)
        if text:
            doc.add_paragraph(text)
    doc.save(docx_path)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload():
    file = request.files['file']
    pdf_path = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(pdf_path)

    html_output_path = os.path.join(OUTPUT_FOLDER, 'output.html')
    docx_output_path = os.path.join(OUTPUT_FOLDER, 'output.docx')

    html_content = process_pdf(pdf_path)

    with open(html_output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)

    html_to_docx(html_output_path, docx_output_path)

    return render_template('result.html', text=html_content, doc_path='output.docx')

@app.route('/download/<path:filename>')
def download(filename):
    return send_from_directory(OUTPUT_FOLDER, filename, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
