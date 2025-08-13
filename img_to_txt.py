import os
import logging
import time
import requests
import pdfplumber

from flask import Flask, request, render_template, send_from_directory
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
from docx import Document
from docx.shared import Inches

# Image OCR imports
from PIL import Image
import google.generativeai as genai

# ── CONFIG & LOGGING ─────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ----------------------------------------------------------------------------------
# IMPORTANT: REPLACE 'YOUR_GEMINI_API_KEY' WITH YOUR ACTUAL GOOGLE AI STUDIO API KEY
# ----------------------------------------------------------------------------------
GEMINI_API_KEY = 'YOUR_GEMINI_API_KEY'

if not GEMINI_API_KEY or GEMINI_API_KEY == 'YOUR_GEMINI_API_KEY':
    logging.critical("FATAL: GEMINI_API_KEY is not set in app.py! The application will not work.")
    # In a real app, you might exit here: exit("API Key not configured.")

# --- SCRIPT CONFIGURATION ---
RETRY_MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 5
CHUNK_SIZE = 4000  # Reduced for better Gemini handling and prompt space

UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'output'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

app = Flask(__name__)

# ── IMAGE OCR FUNCTION (For Image Uploads) ───────────────────────────────────────
def extract_text_from_image_gemini(image_path, model_name="gemini-1.5-flash", prompt="Extract all text from this image exactly as it appears, including formatting and spacing."):
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(model_name)
        img = Image.open(image_path)
        response = model.generate_content([prompt, img])
        return response.text.strip() if response.text else "No text found in image."
    except Exception as e:
        logging.error(f"Error during Gemini Image OCR: {e}")
        return f"Error during OCR: {str(e)}"

# ── TEXT CHUNKING ────────────────────────────────────────────────────────────────
def chunk_text(text, size=CHUNK_SIZE):
    if not text or not text.strip():
        return []
    # Simple chunking by character count, as sentence structure might be complex
    return [text[i:i+size] for i in range(0, len(text), size)]

# ── GEMINI CALL FOR HTML GENERATION ──────────────────────────────────────────────
def call_gemini_for_html(text_chunk: str, retry_count=0) -> str:
    """Invoke Gemini with the detailed user prompt and retry logic."""
    if not text_chunk or not text_chunk.strip():
        return ""
    
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-1.5-flash") # Using a more capable model if needed
        
        # --- THIS IS THE DETAILED PROMPT YOU PROVIDED ---
        detailed_prompt = f"""
You have been given text extracted from a page of an A4-sized document potentially containing Hindi/Sanskrit text. Assume an A4 page as a reference. Your task is to analyze this text and generate an exact HTML replica of how that text might have been structured and formatted on the original page, preserving all formatting details as described below. If the text suggests the presence of an image (e.g., mentions "figure", "image", or has a large gap typical of an image), replace that section with an image placeholder block labeled "Image".

Instructions for Extraction and HTML Formatting:

*   Infer and replicate the structure based *only* on the provided text chunk.
*   Extract every word, number, and special character exactly as it appears in the text chunk.
*   Preserve all spacing, indentation, line breaks, and blank lines exactly as present in the text chunk. Try to infer paragraph breaks from line spacing or indentation. Use <p> tags for paragraphs and <br> for line breaks within paragraphs.
*   Attempt to infer formatting like bold or italics if hinted at by context. Use standard HTML tags (<b>, <i>, <u>). Assume a standard font unless specified otherwise.
*   Infer paragraph alignment (left, right, center, justified) based on text patterns if possible, default to left.
*   If the text looks like a table (e.g., columns of text separated by consistent spacing or tabs), structure it using HTML table tags (<table>, <tr>, <td>). Infer borders if context suggests.
*   If lines start with bullets (like *, -, •, ○, ◘) or numbers (1., a., i.), format them as HTML lists (<ul> or <ol> with <li>).
*   Ensure symbols, special characters, currency signs, checkboxes, arrows, and mathematical notations are accurately represented using HTML entities if necessary.
*   If the text appears to be a header or footer (e.g., page numbers, repeated titles at top/bottom), format it accordingly, perhaps using specific divs with classes like 'header' or 'footer'.
*   If the text contains a line of dashes or similar characters suggesting a horizontal rule, use the <hr> tag. Replicate underlines using <u> or CSS text-decoration.

Handling Potential Images (Inferred from Text):

*   If the text contains phrasing like "[Image]", "Figure X", "See image below", or if there's a significant block of missing text suggested by context, create a div placeholder in HTML.
*   The size of the div must match the original image dimensions if they can be inferred, otherwise use a default.

Example Representation of an Image Placeholder (use if inferred):
<div style="width: 200px; height: 150px; border: 1px solid black; text-align: center; display: flex; align-items: center; justify-content: center; margin: 10px auto;">Image</div>

--- START OF TEXT CHUNK TO CONVERT ---
{text_chunk}
--- END OF TEXT CHUNK TO CONVERT ---

Generate *only* the HTML code for the body content based on the text chunk provided above. Do not include the <html>, <head>, or <body> tags themselves, only the content that would go inside <body>.
"""
        # --- END OF DETAILED PROMPT ---
        
        response = model.generate_content(detailed_prompt)
        
        if response.text:
            # Clean up potential markdown fences
            html_fragment = response.text.strip()
            if html_fragment.startswith("```html"):
                html_fragment = html_fragment[7:]
            if html_fragment.endswith("```"):
                html_fragment = html_fragment[:-3]
            return html_fragment.strip()
        else:
            logging.warning("Empty response from Gemini for chunk. Returning original text as fallback.")
            return f"<pre>{text_chunk}</pre>" # Fallback to preformatted text
            
    except Exception as e:
        logging.error(f"Error during Gemini HTML generation (attempt {retry_count + 1}): {e}")
        if retry_count < RETRY_MAX_ATTEMPTS - 1:
            logging.info(f"Retrying in {RETRY_DELAY_SECONDS} seconds...")
            time.sleep(RETRY_DELAY_SECONDS)
            return call_gemini_for_html(text_chunk, retry_count + 1)
        else:
            return f"<pre style='color:red;'>Error after multiple retries. Original text: \n{text_chunk}</pre>"

# ── PDF PAGE/DOCUMENT PROCESSING ────────────────────────────────────────────────
def process_page(page_num, page) -> str:
    """Processes a single page of a PDF."""
    logging.info(f"Processing page {page_num}")
    try:
        text = page.extract_text()
        if not text or not text.strip():
            logging.warning(f"No text found on page {page_num}")
            return f"<div class='page' data-page-num='{page_num}'><p>No text content found on this page.</p></div>"
        
        chunks = chunk_text(text)
        html_fragments = [call_gemini_for_html(chunk) for chunk in chunks]
        
        return f"<div class='page' data-page-num='{page_num}'>{''.join(html_fragments)}</div>"
    except Exception as e:
        logging.error(f"Error processing page {page_num}: {e}")
        return f"<div class='page' data-page-num='{page_num}'><p style='color:red;'>Error processing page: {str(e)}</p></div>"

def process_pdf(pdf_path: str) -> str:
    """Orchestrates the conversion of a full PDF to a single HTML string."""
    logging.info(f"Starting PDF processing: {pdf_path}")
    try:
        with pdfplumber.open(pdf_path) as pdf:
            with ThreadPoolExecutor(max_workers=4) as executor:
                page_results = list(executor.map(process_page, range(1, len(pdf.pages) + 1), pdf.pages))
            
            combined_html = '\n'.join(page_results)
            
            full_html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>PDF Conversion Result</title>
    <style>
        body {{ background-color: #525659; font-family: sans-serif; }}
        .page {{ 
            background-color: white; 
            box-shadow: 0 0 10px rgba(0,0,0,0.5);
            margin: 20px auto; 
            padding: 40px; 
            width: 210mm; /* A4 width */
            min-height: 297mm; /* A4 height */
        }}
        /* Basic styles inferred from prompt */
        table, th, td {{ border: 1px solid black; border-collapse: collapse; padding: 5px; }}
        pre {{ white-space: pre-wrap; word-wrap: break-word; }}
    </style>
</head>
<body>
    <div class="container">
        {combined_html}
    </div>
</body>
</html>
"""
            logging.info("PDF processing complete.")
            return full_html
    except Exception as e:
        logging.error(f"Fatal error processing PDF: {e}")
        return f"<html><body><h1>Error</h1><p>Could not process PDF: {e}</p></body></html>"

# ── HTML → DOCX CONVERSION ──────────────────────────────────────────────────────
def html_to_docx(html_path: str, docx_path: str):
    """Basic conversion of HTML file to DOCX."""
    try:
        logging.info(f"Converting HTML to DOCX: {html_path}")
        with open(html_path, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f.read(), 'html.parser')
        
        doc = Document()
        body = soup.body if soup.body else soup
        
        for element in body.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'div', 'li']):
            text = element.get_text(strip=True)
            if text:
                if element.name.startswith('h'):
                    doc.add_heading(text, level=int(element.name[1]))
                elif element.name == 'li':
                    doc.add_paragraph(text, style='List Bullet')
                else:
                    doc.add_paragraph(text)
        
        doc.save(docx_path)
        logging.info(f"Successfully saved DOCX: {docx_path}")
    except Exception as e:
        logging.error(f"Error converting HTML to DOCX: {e}")

# ── FLASK ROUTES ────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload():
    try:
        if 'file' not in request.files:
            return render_template('error.html', error="No file part in the request.")
        
        file = request.files['file']
        if file.filename == '':
            return render_template('error.html', error="No file selected for upload.")
        
        filename = file.filename
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)

        _, ext = os.path.splitext(filename.lower())
        
        if ext == '.pdf':
            html_content = process_pdf(filepath)
            
            html_output_path = os.path.join(OUTPUT_FOLDER, 'output.html')
            with open(html_output_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
                
            docx_output_path = os.path.join(OUTPUT_FOLDER, 'output.docx')
            html_to_docx(html_output_path, docx_output_path)
            
            return render_template('result.html', text=html_content, doc_path='output.docx')
        
        elif ext in ['.png', '.jpg', '.jpeg', '.webp']:
            text_from_image = extract_text_from_image_gemini(filepath)
            # Use the HTML generation function on the extracted text
            html_content = call_gemini_for_html(text_from_image)
            return render_template('result.html', text=html_content, doc_path=None)
            
        else:
            return render_template('error.html', error=f"Unsupported file type: '{ext}'. Please upload a PDF or an Image (PNG, JPG, WEBP).")
    
    except Exception as e:
        logging.error(f"An error occurred during upload: {e}", exc_info=True)
        return render_template('error.html', error=str(e))

@app.route('/download/<path:filename>')
def download(filename):
    return send_from_directory(OUTPUT_FOLDER, filename, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True, port=5000)