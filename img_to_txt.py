import os
import logging
import time
import requests
import pdfplumber

from flask import Flask, request, render_template, send_from_directory
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
from docx import Document

# ── CONFIG & LOGGING ─────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s:%(message)s')

GEMINI_API_KEY = 'GEMINI_API_KEY'
if not GEMINI_API_KEY:
    logging.warning("GEMINI_API_KEY is not set! API calls will fail.")

RETRY_MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 5
CHUNK_SIZE = 3000  # Reduced chunk size for better Gemini handling

UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'output'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

app = Flask(__name__)


# ── TEXT CHUNKING ────────────────────────────────────────────────────────────────
def chunk_text(text, size=CHUNK_SIZE):
    sentences = text.split('.')
    chunks = []
    chunk = ""
    for sentence in sentences:
        sentence = sentence.strip() + '.'  # add back the missing '.'
        if len(chunk) + len(sentence) < size:
            chunk += sentence
        else:
            if chunk: #avoid adding empty chunk
                chunks.append(chunk)
            chunk = sentence
    if chunk:
        chunks.append(chunk)
    return chunks


# ── CALL GEMINI ─────────────────────────────────────────────────────────────────
def call_gemini_api(prompt: str, session: requests.Session = None) -> str:
    """Invokes Gemini; returns generated HTML fragment or empty string on failure."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.0-pro:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    if session is None:
        session = requests.Session()

    logging.info("call_gemini_api: invoked")
    for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
        try:
            logging.info(f"Gemini API attempt {attempt}")
            resp = session.post(url, json=payload, timeout=10)  # Added timeout
            resp.raise_for_status()  # Raise HTTPError for bad responses

            response_json = resp.json()

            # Robust error checking of the response structure
            if 'candidates' not in response_json or not isinstance(response_json['candidates'], list) or len(response_json['candidates']) == 0:
                logging.error(f"Gemini API: Invalid response format - missing 'candidates'")
                raise ValueError("Invalid Gemini API response format: missing 'candidates'")

            candidate = response_json['candidates'][0]
            if 'content' not in candidate or 'parts' not in candidate['content'] or not isinstance(candidate['content']['parts'], list) or len(candidate['content']['parts']) == 0:
                logging.error(f"Gemini API: Invalid response format - missing 'content' or 'parts'")
                raise ValueError("Invalid Gemini API response format: missing 'content' or 'parts'")

            text = candidate['content']['parts'][0]['text']

            logging.info("Gemini API call succeeded")
            return text

        except requests.exceptions.HTTPError as e:
            logging.error(f"Gemini API HTTP error on attempt {attempt}: {e}")
            if e.response.status_code == 429:  # Handle rate limiting
                logging.warning("Gemini API: Rate limit exceeded.  Pausing...")
                time.sleep(60)  # Pause longer for rate limits
            elif attempt < RETRY_MAX_ATTEMPTS:
                time.sleep(RETRY_DELAY_SECONDS * (2 ** (attempt - 1)))
            else:
                logging.error("Gemini API: All retries failed due to HTTP error.")
                return ""  # Or re-raise if you want the entire process to fail
        except (requests.exceptions.RequestException, ValueError) as e: # Catch broader exceptions
            logging.error(f"Gemini API error on attempt {attempt}: {e}")
            if attempt < RETRY_MAX_ATTEMPTS:
                time.sleep(RETRY_DELAY_SECONDS * (2 ** (attempt - 1)))
            else:
                logging.error("Gemini API: All retries failed, returning empty string")
                return ""

    logging.error("Gemini API: all retries failed, returning empty string")
    return ""


# ── PDF → HTML ──────────────────────────────────────────────────────────────────
def process_page(page, session: requests.Session) -> str:
    text = page.extract_text() or ""
    html = ""
    for chunk in chunk_text(text):
        prompt = f"""
You have been given text extracted from a page of an A4-sized document potentially containing Hindi/Sanskrit text. Assume an A4 page as a reference. Your task is to analyze this text and generate an exact HTML replica of how that text might have been structured and formatted on the original page, preserving all formatting details as described below. If the text suggests the presence of an image (e.g., mentions "figure", "image", or has a large gap typical of an image), replace that section with an image placeholder block labeled "Image".

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
        fragment = call_gemini_api(prompt, session=session)
        if fragment: #avoid adding empty fragments
          html += fragment
    return f"<div>{html}</div>"


def process_pdf(pdf_path: str) -> str:
    logging.info(f"Processing PDF: {pdf_path}")
    all_html = ""
    try:
        with requests.Session() as session:  # Create a session here
            with pdfplumber.open(pdf_path) as pdf:
                with ThreadPoolExecutor(max_workers=4) as execr:
                    futures = [execr.submit(process_page, p, session) for p in pdf.pages]  # Pass the session
                    for future in futures:
                        all_html += future.result()
        full = f"<html><body>{all_html}</body></html>"
        logging.info(f"Generated HTML length: {len(full)} characters")
        return full
    except Exception as e:
        logging.error(f"Error processing PDF: {e}")
        return "<p>Error processing PDF.  See logs for details.</p>" # Return an error message to display to user

# ── HTML → DOCX ────────────────────────────────────────────────────────────────
def html_to_docx(html_path: str, docx_path: str):
    try:
        with open(html_path, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f, 'html.parser')

        # grab everything under <body>, fallback to full document
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
        pdf_path = os.path.join(UPLOAD_FOLDER, file.filename)
        file.save(pdf_path)

        html_output = os.path.join(OUTPUT_FOLDER, 'output.html')
        docx_output = os.path.join(OUTPUT_FOLDER, 'output.docx')

        # Process
        html_content = process_pdf(pdf_path)
        with open(html_output, 'w', encoding='utf-8') as f:
            f.write(html_content)

        html_to_docx(html_output, docx_output)

        return render_template('result.html', text=html_content, doc_path='output.docx')

    except Exception as e:
        logging.error(f"Upload error: {e}")
        return render_template('error.html', error=str(e)) # Handle errors and display to the user


@app.route('/download/<path:filename>')
def download(filename):
    return send_from_directory(OUTPUT_FOLDER, filename, as_attachment=True)


if __name__ == '__main__':
    app.run(debug=True)