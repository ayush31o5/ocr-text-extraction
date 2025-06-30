import os
import logging
import time
import requests
import pdfplumber

from flask import Flask, request, render_template, send_from_directory
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
from docx import Document

# Image OCR imports
from PIL import Image
import google.generativeai as genai

# ── CONFIG & LOGGING ─────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s:%(message)s')

GEMINI_API_KEY = 'AIzaSyAHBYZGkBWwBaSCt4rXyvDA3sQfjSwJGro'
if GEMINI_API_KEY == 'AIzaSyAHBYZGkBWwBaSCt4rXyvDA3sQfjSwJGro':
    logging.warning("GEMINI_API_KEY is not set! API calls will fail.")

RETRY_MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 5
CHUNK_SIZE = 3000  # Reduced chunk size for better Gemini handling

UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'output'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

app = Flask(__name__)

# ── IMAGE OCR FUNCTION ───────────────────────────────────────────────────────────
def extract_text_from_image_gemini(image_path, api_key=None, model_name="gemini-1.5-flash", prompt="Extract the text from this image, including any emojis."):
    if api_key is None:
        api_key = GEMINI_API_KEY
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name)
    try:
        img = Image.open(image_path)
        response = model.generate_content([prompt, img])
        response.resolve()
        return response.text.strip()
    except Exception as e:
        logging.error(f"Error during Gemini OCR: {e}")
        return ""

# ── TEXT CHUNKING ────────────────────────────────────────────────────────────────
def chunk_text(text, size=CHUNK_SIZE):
    sentences = text.split('.')
    chunks = []
    chunk = ""
    for sentence in sentences:
        sentence = sentence.strip() + '.'
        if len(chunk) + len(sentence) < size:
            chunk += sentence
        else:
            if chunk:
                chunks.append(chunk)
            chunk = sentence
    if chunk:
        chunks.append(chunk)
    return chunks

# ── SIMPLE GEMINI CALL FOR PDF PROCESSING ─────────────────────────────────────────
def call_gemini_for_html(prompt: str) -> str:
    """Invoke Gemini directly and return HTML fragment or empty string."""
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")
    try:
        response = model.generate_content([prompt])
        response.resolve()
        return response.text
    except Exception as e:
        logging.error(f"Error during Gemini HTML generation: {e}")
        return ""

# ── SINGLE PAGE PROCESSING ───────────────────────────────────────────────────────
def process_page(page) -> str:
    text = page.extract_text() or ""
    html = ""
    for chunk in chunk_text(text):
        prompt = f"""
You have been given text extracted from a page of an A4-sized document potentially containing Hindi/Sanskrit text. Assume an A4 page as a reference. Your task is to analyze this text and generate an exact HTML replica of how that text might have been structured and formatted on the original page, preserving all formatting details as described below. If the text suggests the presence of an image (e.g., mentions \"figure\", \"image\", or has a large gap typical of an image), replace that section with an image placeholder block labeled \"Image\".

Instructions for Extraction and HTML Formatting:

    Infer and replicate the structure based *only* on the provided text chunk.
    Extract every word, number, and special character exactly as it appears in the text chunk.
    Preserve all spacing, indentation, line breaks, and blank lines exactly as present in the text chunk. Try to infer paragraph breaks from line spacing or indentation.
    Attempt to infer formatting like bold or italics if hinted at by context. Use standard HTML tags (<b>, <i>, <u>).
    Infer paragraph alignment (left, right, center, justified) based on text patterns if possible, default to left.
    If the text looks like a table, structure it using HTML table tags (<table>, <tr>, <td>).
    If lines start with bullets or numbers, format them as HTML lists (<ul> or <ol> with <li>).
    Ensure symbols, special characters, currency signs, checkboxes, arrows, and mathematical notations are accurately represented.
    If the text appears to be a header or footer, format it accordingly.
    If the text contains a line of dashes suggesting a horizontal rule, use the <hr> tag.

Handling Potential Images:

    If the text contains phrasing like "[Image]", "Figure X", "See image below", create a div placeholder with "Image" inside it:

<div style="width:200px;height:150px;border:1px solid black;text-align:center;display:flex;align-items:center;justify-content:center;margin:10px auto;">Image Placeholder</div>

--- START OF TEXT CHUNK TO CONVERT ---

{chunk}

--- END OF TEXT CHUNK TO CONVERT ---

Generate only the HTML code for the body content based on the text chunk provided above.
"""
        fragment = call_gemini_for_html(prompt)
        if fragment:
            html += fragment
    return f"<div>{html}</div>"

# ── PDF → HTML ──────────────────────────────────────────────────────────────────
def process_pdf(pdf_path: str) -> str:
    logging.info(f"Starting PDF processing: {pdf_path}")
    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages = pdf.pages
            total = len(pages)
            logging.info(f"Total pages detected: {total}")
            all_html = ""
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(process_page, p) for p in pages]
                for idx, future in enumerate(futures, start=1):
                    html_fragment = future.result()
                    all_html += html_fragment
                    logging.info(f"Processed page {idx}/{total}")
        full = f"<html><body>{all_html}</body></html>"
        logging.info(f"PDF processing complete, generated {total} page fragments")
        return full
    except Exception as e:
        logging.error(f"Error processing PDF: {e}")
        return "<p>Error processing PDF. See logs for details.</p>"

# ── HTML → DOCX ────────────────────────────────────────────────────────────────
def html_to_docx(html_path: str, docx_path: str):
    try:
        with open(html_path, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f, 'html.parser')

        body = soup.body or soup
        full_text = body.get_text(separator='\n')

        doc = Document()
        for line in full_text.split('\n'):
            line = line.strip()
            if line:
                doc.add_paragraph(line)
        doc.save(docx_path)
        logging.info(f"Saved DOCX: {docx_path}")
    except Exception as e:
        logging.error(f"Error converting HTML to DOCX: {e}")

# ── FLASK ENDPOINTS ─────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload():
    try:
        file = request.files['file']
        filename = file.filename
        _, ext = os.path.splitext(filename.lower())

        if ext in ['.png', '.jpg', '.jpeg', '.bmp', '.gif']:
            image_path = os.path.join(UPLOAD_FOLDER, filename)
            file.save(image_path)
            text = extract_text_from_image_gemini(image_path)
            return render_template('result.html', text=f"<pre>{text}</pre>")
        else:
            pdf_path = os.path.join(UPLOAD_FOLDER, filename)
            file.save(pdf_path)

            html_output = os.path.join(OUTPUT_FOLDER, 'output.html')
            docx_output = os.path.join(OUTPUT_FOLDER, 'output.docx')

            html_content = process_pdf(pdf_path)
            with open(html_output, 'w', encoding='utf-8') as f:
                f.write(html_content)

            html_to_docx(html_output, docx_output)

            return render_template('result.html', text=html_content, doc_path='output.docx')

    except Exception as e:
        logging.error(f"Upload error: {e}")
        return render_template('error.html', error=str(e))

@app.route('/download/<path:filename>')
def download(filename):
    return send_from_directory(OUTPUT_FOLDER, filename, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
