import os
import time
import pdfplumber
from bs4 import BeautifulSoup
import requests
import docx
from docx.shared import Inches
from flask import Flask, request, render_template, send_from_directory, redirect, url_for

GEMINI_API_KEY = "YOUR_GEMINI_API_KEY" # Ensure you have an API key

OUTPUT_DIR = "output"
CHUNK_SIZE = 15000
RETRY_MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 5

def chunk_text(text, chunk_size=CHUNK_SIZE):
    """Splits text into chunks of approximately chunk_size characters, respecting sentences."""
    sentences = text.split('.')
    chunks = []
    current_chunk = ""
    for sentence in sentences:
        if len(current_chunk) + len(sentence) + 1 <= chunk_size:
            current_chunk += sentence + "."
        else:
            chunks.append(current_chunk.strip())
            current_chunk = sentence + "."
    if current_chunk:
        chunks.append(current_chunk.strip())
    return chunks

def call_gemini_api(prompt):
    """Calls the Gemini Pro API with retry logic."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={GEMINI_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    data = {
        "contents": [{
            "parts": [{"text": prompt}]
        }]
    }

    for attempt in range(RETRY_MAX_ATTEMPTS):
        try:
            response = requests.post(url, headers=headers, json=data)
            response.raise_for_status()
            gemini_output = response.json()
            return gemini_output['candidates'][0]['content']['parts'][0]['text']
        except requests.exceptions.RequestException as e:
            print(f"API request error (attempt {attempt + 1}/{RETRY_MAX_ATTEMPTS}): {e}")
            if attempt < RETRY_MAX_ATTEMPTS - 1:
                print(f"Retrying in {RETRY_DELAY_SECONDS} seconds...")
                time.sleep(RETRY_DELAY_SECONDS)
            else:
                raise  # Re-raise the last exception
        except KeyError as e:
            print(f"Error parsing API response (attempt {attempt + 1}/{RETRY_MAX_ATTEMPTS}): {e}")
            print(f"Raw response: {response.text if 'response' in locals() else 'No response'}")
            if attempt < RETRY_MAX_ATTEMPTS - 1:
                print(f"Retrying in {RETRY_DELAY_SECONDS} seconds...")
                time.sleep(RETRY_DELAY_SECONDS)
            else:
                raise  # Re-raise the last exception

    return None # Should not reach here

def pdf_to_html_with_gemini(pdf_path, output_dir=OUTPUT_DIR):
    """
    Converts a PDF to HTML using Gemini Pro for HTML generation, one page at a time.

    Args:
        pdf_path (str): Path to the input PDF file.
        output_dir (str, optional): Directory to store the output files. Defaults to OUTPUT_DIR.
    """

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    all_html_content = ""  # Accumulate HTML content from all pages.

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_num in range(len(pdf.pages)):
                page = pdf.pages[page_num]
                text = page.extract_text()

                if not text:
                    print(f"Warning: Page {page_num + 1} is empty. Skipping.")
                    continue

                print(f"Processing page {page_num + 1}...")

                # Chunk the extracted text from the page
                text_chunks = chunk_text(text)
                page_html_content = ""

                for i, chunk in enumerate(text_chunks):
                    prompt = (
                        "Convert the following text into valid and well-structured HTML code, "
                        "preserving formatting as closely as possible and use CSS inline where appropriate. "
                        "Do not include HTML, BODY and HEAD tags: \n\n"
                        f"{chunk}"
                    )
                    try:
                        html_fragment = call_gemini_api(prompt)
                        if html_fragment:
                            page_html_content += html_fragment
                        else:
                            print(f"Warning: Failed to get HTML for chunk {i+1} of page {page_num + 1}.")
                    except Exception as e:
                        print(f"Error processing chunk {i+1} of page {page_num + 1}: {e}")

                # Append the HTML content of the current page to the overall HTML content
                all_html_content += f"<div>{page_html_content}</div>"
    except FileNotFoundError:
        print(f"Error: PDF file not found at {pdf_path}")
        return
    except Exception as e:
        print(f"An error occurred during PDF processing: {e}")
        return

    # Wrap the accumulated HTML content with basic HTML structure
    final_html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Converted PDF</title>
    </head>
    <body>
        {all_html_content}
    </body>
    </html>
    """

    # Save the final HTML to a file
    html_output_path = os.path.join(output_dir, "output.html")
    with open(html_output_path, "w", encoding="utf-8") as f:
        f.write(final_html_content)
    print(f"HTML saved to {html_output_path}")

    # Convert the generated HTML to DOCX
    docx_output_path = os.path.join(output_dir, "output.docx")
    html_to_docx(html_output_path, docx_output_path)  # Use the existing function
    print(f"DOCX file saved to {docx_output_path}")

def html_to_docx(html_file, docx_file):
    """Converts an HTML file to a DOCX file (text content only)."""
    try:
        with open(html_file, 'r', encoding='utf-8') as f:
            html_content = f.read()

        soup = BeautifulSoup(html_content, 'html.parser')
        text = soup.get_text(separator='\n') # Get all text with line breaks

        document = docx.Document()
        for paragraph in text.splitlines():
            if paragraph.strip():
                document.add_paragraph(paragraph)
        document.save(docx_file)

    except FileNotFoundError:
        print(f"Error: HTML file not found at {html_file}")
    except Exception as e:
        print(f"An error occurred during HTML to DOCX conversion: {e}")

# Flask Application Integration

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return "No file part", 400
    file = request.files['file']
    if file.filename == '':
        return "No selected file", 400
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
    file.save(filepath)
    
    # Process the PDF file
    pdf_to_html_with_gemini(filepath)
    
    # Read the generated HTML content to display
    html_output_path = os.path.join(OUTPUT_DIR, "output.html")
    extracted_text = ""
    if os.path.exists(html_output_path):
        with open(html_output_path, 'r', encoding='utf-8') as f:
            extracted_text = f.read()
    
    # Path for the generated DOCX file (for download link)
    docx_output_path = os.path.join(OUTPUT_DIR, "output.docx")
    
    return render_template('result.html', text=extracted_text, doc_path=docx_output_path)

@app.route('/download/<path:filename>')
def download(filename):
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
