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
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s:%(message)s')

# Replace with your actual API key
GEMINI_API_KEY = 'AIzaSyAHBYZGkBWwBaSCt4rXyvDA3sQfjSwJGro'
if GEMINI_API_KEY == 'AIzaSyAHBYZGkBWwBaSCt4rXyvDA3sQfjSwJGro':
    logging.warning("GEMINI_API_KEY is not set! API calls will fail.")

RETRY_MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 2
CHUNK_SIZE = 2000  # Reduced chunk size for better Gemini handling

UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'output'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

app = Flask(__name__)

# ── IMAGE OCR FUNCTION ───────────────────────────────────────────────────────────
def extract_text_from_image_gemini(image_path, api_key=None, model_name="gemini-1.5-flash", prompt="Extract all text from this image exactly as it appears, including formatting, spacing, and any special characters or emojis."):
    if api_key is None:
        api_key = 'AIzaSyAHBYZGkBWwBaSCt4rXyvDA3sQfjSwJGro'
    
    if not api_key or api_key == 'AIzaSyAHBYZGkBWwBaSCt4rXyvDA3sQfjSwJGro':
        logging.error("No valid API key provided for Gemini")
        return "Error: No valid API key"
    
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
        
        img = Image.open(image_path)
        response = model.generate_content([prompt, img])
        
        if response.text:
            return response.text.strip()
        else:
            logging.error("Empty response from Gemini")
            return "Error: Empty response from Gemini"
            
    except Exception as e:
        logging.error(f"Error during Gemini OCR: {e}")
        return f"Error during OCR: {str(e)}"

# ── TEXT CHUNKING ────────────────────────────────────────────────────────────────
def chunk_text(text, size=CHUNK_SIZE):
    if not text or len(text.strip()) == 0:
        return []
    
    # Split by sentences first, then by paragraphs
    sentences = text.replace('\n\n', ' ||PARAGRAPH|| ').split('.')
    chunks = []
    current_chunk = ""
    
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
            
        # Restore paragraph breaks
        sentence = sentence.replace('||PARAGRAPH||', '\n\n')
        sentence_with_period = sentence + '.' if not sentence.endswith('.') else sentence
        
        if len(current_chunk) + len(sentence_with_period) < size:
            current_chunk += sentence_with_period + ' '
        else:
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
            current_chunk = sentence_with_period + ' '
    
    if current_chunk.strip():
        chunks.append(current_chunk.strip())
    
    return chunks

# ── IMPROVED GEMINI CALL FOR PDF PROCESSING ─────────────────────────────────────────
def call_gemini_for_html(text_chunk: str, retry_count=0) -> str:
    """Invoke Gemini with retry logic and return HTML fragment."""
    if not GEMINI_API_KEY or GEMINI_API_KEY == 'YOUR_ACTUAL_API_KEY_HERE':
        logging.error("No valid API key for Gemini")
        return f"<p>Error: No valid API key. Text content: {text_chunk}</p>"
    
    if not text_chunk or len(text_chunk.strip()) == 0:
        return ""
    
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-1.5-flash")
        
        prompt = f"""
Convert the following text into clean, properly formatted HTML. Follow these rules:

1. Preserve all text content exactly as provided
2. Create proper HTML structure with appropriate tags
3. Use <p> tags for paragraphs
4. Use <h1>, <h2>, <h3> for headings based on text formatting
5. Use <ul> and <li> for bullet points
6. Use <ol> and <li> for numbered lists
7. Use <table>, <tr>, <td> for tabular data
8. Use <strong> for bold text, <em> for italic text
9. Preserve line breaks and spacing
10. If text suggests an image location, use: <div class="image-placeholder">[Image Placeholder]</div>

Text to convert:
{text_chunk}

Return only the HTML content, no explanations:
"""
        
        response = model.generate_content(prompt)
        
        if response.text:
            return response.text.strip()
        else:
            logging.warning(f"Empty response from Gemini for chunk")
            return f"<p>{text_chunk}</p>"  # Fallback to basic HTML
            
    except Exception as e:
        logging.error(f"Error during Gemini HTML generation (attempt {retry_count + 1}): {e}")
        
        if retry_count < RETRY_MAX_ATTEMPTS - 1:
            logging.info(f"Retrying in {RETRY_DELAY_SECONDS} seconds...")
            time.sleep(RETRY_DELAY_SECONDS)
            return call_gemini_for_html(text_chunk, retry_count + 1)
        else:
            # Fallback to basic HTML if all retries fail
            return f"<p>{text_chunk}</p>"

# ── SINGLE PAGE PROCESSING ───────────────────────────────────────────────────────
def process_page(page_num, page) -> str:
    logging.info(f"Processing page {page_num}")
    
    try:
        # Extract text from the page
        text = page.extract_text()
        
        if not text or len(text.strip()) == 0:
            logging.warning(f"No text found on page {page_num}")
            return f"<div class='page'><p>No text content found on page {page_num}</p></div>"
        
        logging.info(f"Page {page_num}: Extracted {len(text)} characters")
        
        # Process text in chunks
        chunks = chunk_text(text)
        if not chunks:
            return f"<div class='page'><p>{text}</p></div>"
        
        html_fragments = []
        for i, chunk in enumerate(chunks):
            logging.info(f"Page {page_num}, processing chunk {i+1}/{len(chunks)}")
            html_fragment = call_gemini_for_html(chunk)
            if html_fragment:
                html_fragments.append(html_fragment)
        
        combined_html = '\n'.join(html_fragments)
        return f"<div class='page' data-page='{page_num}'>{combined_html}</div>"
        
    except Exception as e:
        logging.error(f"Error processing page {page_num}: {e}")
        return f"<div class='page'><p>Error processing page {page_num}: {str(e)}</p></div>"

# ── PDF → HTML ──────────────────────────────────────────────────────────────────
def process_pdf(pdf_path: str) -> str:
    logging.info(f"Starting PDF processing: {pdf_path}")
    
    if not os.path.exists(pdf_path):
        logging.error(f"PDF file not found: {pdf_path}")
        return "<html><body><p>Error: PDF file not found</p></body></html>"
    
    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages = pdf.pages
            total_pages = len(pages)
            logging.info(f"Total pages detected: {total_pages}")
            
            if total_pages == 0:
                return "<html><body><p>Error: PDF contains no pages</p></body></html>"
            
            all_html_fragments = []
            
            # Process pages with limited concurrency to avoid API rate limits
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = []
                for idx, page in enumerate(pages):
                    future = executor.submit(process_page, idx + 1, page)
                    futures.append(future)
                
                for future in futures:
                    try:
                        html_fragment = future.result(timeout=60)  # 60 second timeout
                        all_html_fragments.append(html_fragment)
                    except Exception as e:
                        logging.error(f"Error getting result from future: {e}")
                        all_html_fragments.append(f"<div class='page'><p>Error processing page: {str(e)}</p></div>")
            
            combined_html = '\n'.join(all_html_fragments)
            
            full_html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>PDF Conversion Result</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        .page {{ margin-bottom: 30px; padding: 20px; border: 1px solid #ccc; }}
        .image-placeholder {{ 
            width: 200px; 
            height: 150px; 
            border: 1px solid #000; 
            text-align: center; 
            display: flex; 
            align-items: center; 
            justify-content: center; 
            margin: 10px auto; 
            background-color: #f0f0f0;
        }}
        table {{ border-collapse: collapse; width: 100%; }}
        td, th {{ border: 1px solid #ddd; padding: 8px; }}
    </style>
</head>
<body>
{combined_html}
</body>
</html>
"""
            
            logging.info(f"PDF processing complete. Generated HTML with {len(all_html_fragments)} page fragments")
            return full_html
            
    except Exception as e:
        logging.error(f"Error processing PDF: {e}")
        return f"<html><body><p>Error processing PDF: {str(e)}</p></body></html>"

# ── IMPROVED HTML → DOCX ────────────────────────────────────────────────────────────
def html_to_docx(html_path: str, docx_path: str):
    try:
        logging.info(f"Converting HTML to DOCX: {html_path} -> {docx_path}")
        
        with open(html_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        if not html_content or len(html_content.strip()) == 0:
            logging.error("HTML file is empty")
            return
        
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Remove script and style tags
        for script in soup(["script", "style"]):
            script.decompose()
        
        doc = Document()
        
        # Process the HTML content
        body = soup.body if soup.body else soup
        
        for element in body.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'div', 'li']):
            text = element.get_text().strip()
            if text:
                if element.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                    # Add heading
                    heading = doc.add_heading(text, level=int(element.name[1]))
                elif element.name == 'li':
                    # Add list item (simplified)
                    doc.add_paragraph(f"• {text}")
                else:
                    # Add regular paragraph
                    doc.add_paragraph(text)
        
        # If no structured content found, add all text as paragraphs
        if len(doc.paragraphs) == 0:
            full_text = soup.get_text()
            for line in full_text.split('\n'):
                line = line.strip()
                if line:
                    doc.add_paragraph(line)
        
        doc.save(docx_path)
        logging.info(f"Successfully saved DOCX: {docx_path}")
        
    except Exception as e:
        logging.error(f"Error converting HTML to DOCX: {e}")
        # Create a simple document with error message
        try:
            doc = Document()
            doc.add_paragraph(f"Error converting HTML to DOCX: {str(e)}")
            doc.save(docx_path)
        except:
            pass

# ── FLASK ENDPOINTS ─────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload():
    try:
        if 'file' not in request.files:
            return render_template('error.html', error="No file uploaded")
        
        file = request.files['file']
        if file.filename == '':
            return render_template('error.html', error="No file selected")
        
        filename = file.filename
        _, ext = os.path.splitext(filename.lower())
        
        if ext in ['.png', '.jpg', '.jpeg', '.bmp', '.gif', '.tiff', '.webp', '.svg', '.ico']:
            # Handle image files
            image_path = os.path.join(UPLOAD_FOLDER, filename)
            file.save(image_path)
            
            text = extract_text_from_image_gemini(image_path)
            
            # Create a simple HTML display for the extracted text
            html_text = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <title>Image OCR Result</title>
                <style>
                    body {{ font-family: Arial, sans-serif; margin: 20px; }}
                    .image-result {{ 
                        border: 1px solid #ccc; 
                        padding: 20px; 
                        background-color: #f9f9f9; 
                        white-space: pre-wrap;
                        word-wrap: break-word;
                    }}
                    .image-info {{
                        color: #666;
                        font-size: 14px;
                        margin-bottom: 10px;
                    }}
                </style>
            </head>
            <body>
                <div class="image-info">Extracted text from image: {filename}</div>
                <div class="image-result">{text}</div>
            </body>
            </html>
            """
            
            return render_template('result.html', text=html_text)
            
        elif ext == '.pdf':
            # Handle PDF files
            pdf_path = os.path.join(UPLOAD_FOLDER, filename)
            file.save(pdf_path)
            
            html_output = os.path.join(OUTPUT_FOLDER, 'output.html')
            docx_output = os.path.join(OUTPUT_FOLDER, 'output.docx')
            
            # Process PDF to HTML
            html_content = process_pdf(pdf_path)
            
            # Save HTML file
            with open(html_output, 'w', encoding='utf-8') as f:
                f.write(html_content)
            
            # Convert HTML to DOCX
            html_to_docx(html_output, docx_output)
            
            return render_template('result.html', text=html_content, doc_path='output.docx')
        
        else:
            return render_template('error.html', error=f"Unsupported file type: {ext}. Supported formats: PDF, PNG, JPG, JPEG, BMP, GIF, TIFF, WEBP, SVG, ICO")
    
    except Exception as e:
        logging.error(f"Upload error: {e}")
        return render_template('error.html', error=str(e))

@app.route('/download/<path:filename>')
def download(filename):
    return send_from_directory(OUTPUT_FOLDER, filename, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True, port=5000)