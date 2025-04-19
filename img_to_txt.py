import os
import time
import pdfplumber
from bs4 import BeautifulSoup
import requests
import docx
from docx.shared import Inches
from flask import Flask, request, render_template, send_from_directory

GEMINI_API_KEY = "YOUR_GEMINI_API_KEY"  # Replace with your actual API key

OUTPUT_DIR = "output"
CHUNK_SIZE = 15000 # Adjust based on typical page content and API limits
RETRY_MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 5
UPLOAD_FOLDER = 'uploads'

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
            # Important: Check if candidates list exists and is not empty
            if 'candidates' in gemini_output and gemini_output['candidates']:
                 # Further check if 'content' and 'parts' exist
                 if 'content' in gemini_output['candidates'][0] and 'parts' in gemini_output['candidates'][0]['content']:
                     return gemini_output['candidates'][0]['content']['parts'][0]['text']
                 else:
                     print(f"Warning: Unexpected response structure (missing content/parts): {gemini_output}")
                     return None # Indicate failure or empty response
            else:
                 # Handle cases where the API might return an empty or blocked response
                 print(f"Warning: No candidates found in API response (attempt {attempt + 1}/{RETRY_MAX_ATTEMPTS}). Response: {gemini_output}")
                 # You might want to return None or an empty string here depending on desired behavior
                 # If it's a safety block, the response might contain 'promptFeedback'
                 if 'promptFeedback' in gemini_output:
                     print(f"Prompt Feedback: {gemini_output['promptFeedback']}")
                 return None # Indicate failure or empty response


        except requests.exceptions.RequestException as e:
            print(f"API request error (attempt {attempt + 1}/{RETRY_MAX_ATTEMPTS}): {e}")
            if attempt < RETRY_MAX_ATTEMPTS - 1:
                print(f"Retrying in {RETRY_DELAY_SECONDS} seconds...")
                time.sleep(RETRY_DELAY_SECONDS)
            else:
                raise  # Re-raise the last exception
        except KeyError as e:
            # This might be less likely now with the checks above, but kept for safety
            print(f"Error parsing API response (attempt {attempt + 1}/{RETRY_MAX_ATTEMPTS}): {e}")
            print(f"Raw response: {response.text if 'response' in locals() else 'No response'}")
            if attempt < RETRY_MAX_ATTEMPTS - 1:
                print(f"Retrying in {RETRY_DELAY_SECONDS} seconds...")
                time.sleep(RETRY_DELAY_SECONDS)
            else:
                raise  # Re-raise the last exception
        except Exception as e: # Catch any other unexpected errors during API call/processing
             print(f"Unexpected error during API call (attempt {attempt+1}/{RETRY_MAX_ATTEMPTS}): {e}")
             if attempt < RETRY_MAX_ATTEMPTS - 1:
                 print(f"Retrying in {RETRY_DELAY_SECONDS} seconds...")
                 time.sleep(RETRY_DELAY_SECONDS)
             else:
                 raise # Re-raise the last exception


    return None # Should not reach here if all retries fail

def pdf_to_html_with_gemini(pdf_path, output_dir=OUTPUT_DIR):
    """
    Converts a PDF to HTML using Gemini Pro with a detailed formatting prompt.

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
                # Extract text using pdfplumber. This gets the text content.
                # Note: pdfplumber primarily extracts text, not precise visual formatting
                # or image data/dimensions needed for the detailed prompt.
                # The LLM will try to infer formatting from the text structure.
                text = page.extract_text()

                if not text:
                    print(f"Warning: Page {page_num + 1} is empty or couldn't extract text. Skipping.")
                    continue

                print(f"Processing page {page_num + 1}...")

                # Chunk the extracted text from the page
                text_chunks = chunk_text(text)
                page_html_content = ""

                for i, chunk in enumerate(text_chunks):
                    # --- Use the new detailed prompt ---
                    detailed_prompt = f"""
You have been given text extracted from a page of an A4-sized document potentially containing Hindi/Sanskrit text. Assume an A4 page as a reference. Your task is to analyze this text and generate an exact HTML replica of how that text might have been structured and formatted on the original page, preserving all formatting details as described below. If the text suggests the presence of an image (e.g., mentions "figure", "image", or has a large gap typical of an image), replace that section with an image placeholder block labeled "Image".

Instructions for Extraction and HTML Formatting:

    Infer and replicate the structure based *only* on the provided text chunk.
    Extract every word, number, and special character exactly as it appears in the text chunk.
    Preserve all spacing, indentation, line breaks, and blank lines exactly as present in the text chunk. Try to infer paragraph breaks from line spacing or indentation.
    Attempt to infer formatting like bold or italics if hinted at by context (though this is difficult from plain text). Use standard HTML tags (<b>, <i>, <u>). Assume a standard font unless specified otherwise.
    Infer paragraph alignment (left, right, center, justified) based on text patterns if possible, default to left.
    If the text looks like a table (e.g., columns of text separated by consistent spacing or tabs), structure it using HTML table tags (<table>, <tr>, <td>). Infer borders if context suggests.
    If lines start with bullets (like *, -, •, ○, ◘) or numbers (1., a., i.), format them as HTML lists (<ul> or <ol> with <li>).
    Ensure symbols, special characters, currency signs, checkboxes, arrows, and mathematical notations are accurately represented using HTML entities if necessary.
    If the text appears to be a header or footer (e.g., page numbers, repeated titles at top/bottom), format it accordingly, perhaps using <header> or <footer> tags or specific divs.
    If the text contains a line of dashes or similar characters suggesting a horizontal rule, use the <hr> tag. Replicate underlines using <u> or CSS text-decoration.

Handling Potential Images (Inferred from Text):

    If the text contains phrasing like "[Image]", "Figure X", "See image below", or if there's a significant block of missing text suggested by context or page layout description (if available), create a div placeholder in HTML with the text "Image" written inside it.
    Since exact dimensions aren't available from text alone, use a default placeholder size or try to infer a reasonable size if the text gives clues.

Example Representation of an Image Placeholder (use if inferred):

<div style="width: 200px; height: 150px; border: 1px solid black; text-align: center; display: flex; align-items: center; justify-content: center; margin: 10px auto;">Image Placeholder</div>

--- START OF TEXT CHUNK TO CONVERT ---

{chunk}

--- END OF TEXT CHUNK TO CONVERT ---

Generate *only* the HTML code for the body content based on the text chunk provided above. Do not include the <html>, <head>, or <body> tags themselves, only the content that would go inside <body>.
"""
                    # --- End of new detailed prompt ---

                    try:
                        html_fragment = call_gemini_api(detailed_prompt)
                        if html_fragment:
                            # Basic cleaning: Remove potential markdown code fences if Gemini adds them
                            html_fragment = html_fragment.strip()
                            if html_fragment.startswith("```html"):
                                html_fragment = html_fragment[7:]
                            if html_fragment.endswith("```"):
                                html_fragment = html_fragment[:-3]
                            page_html_content += html_fragment.strip() + "\n" # Add newline between fragments
                        else:
                            print(f"Warning: Failed to get HTML for chunk {i+1} of page {page_num + 1}.")
                    except Exception as e:
                        print(f"Error processing chunk {i+1} of page {page_num + 1}: {e}")

                # Append the HTML content of the current page to the overall HTML content
                # Wrap each page's content in a div for potential styling/separation
                all_html_content += f'<div class="page-content" style="border: 1px solid #ccc; margin-bottom: 20px; padding: 15px;">\n{page_html_content}\n</div>\n'

    except pdfplumber.exceptions.PDFSyntaxError as pdf_err:
        print(f"Error: Invalid or corrupted PDF file: {pdf_path} - {pdf_err}")
        return None
    except FileNotFoundError:
        print(f"Error: PDF file not found at {pdf_path}")
        return None
    except Exception as e:
        print(f"An error occurred during PDF processing: {e}")
        return None

    # Wrap the accumulated HTML content with basic HTML structure
    final_html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Converted PDF - Gemini Pro HTML Replica</title>
        <style>
            body {{ font-family: sans-serif; line-height: 1.4; }}
            .page-content {{ background-color: #f9f9f9; }}
            /* Add any other basic styles needed for viewing */
            table, th, td {{ border: 1px solid black; border-collapse: collapse; padding: 5px; }}
            ul, ol {{ margin-left: 20px; }}
        </style>
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
    return html_output_path

def html_to_docx(html_file, docx_file):
    """Converts an HTML file to a DOCX file (basic text and paragraph conversion)."""
    try:
        with open(html_file, 'r', encoding='utf-8') as f:
            html_content = f.read()

        # Use BeautifulSoup to parse the HTML
        soup = BeautifulSoup(html_content, 'html.parser')

        document = docx.Document()

        # Find the main body content (or process all relevant tags)
        body_content = soup.find('body')
        if not body_content:
             # Fallback if no body tag found (shouldn't happen with our structure)
            body_content = soup

        # --- Basic conversion - focus on paragraphs ---
        # This is a simplification. A full HTML-to-DOCX converter
        # would need to handle tables, lists, styles etc., much more robustly.
        # Libraries like 'html2docx' or 'pypandoc' (requires Pandoc install)
        # offer more advanced conversion but add complexity.

        for element in body_content.find_all(['p', 'div', 'pre', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'td']):
             # Extract text, preserving some basic structure with newlines
             text = element.get_text(separator='\n', strip=True)
             if text: # Add non-empty paragraphs
                 document.add_paragraph(text)
             # Add a blank paragraph for visual separation between some elements
             # You might want more sophisticated logic here
             # if element.name in ['div', 'p', 'pre', 'h1', 'h2', 'h3']:
             #    document.add_paragraph() # Add extra space

        # --- Attempt basic table conversion (Example - needs refinement) ---
        for table in body_content.find_all('table'):
            try:
                rows = table.find_all('tr')
                if not rows: continue
                # Assuming first row might be header
                cols_count = len(rows[0].find_all(['td', 'th']))
                if cols_count == 0: continue

                doc_table = document.add_table(rows=len(rows), cols=cols_count)
                doc_table.style = 'Table Grid' # Apply a basic style

                for i, row in enumerate(rows):
                    cells = row.find_all(['td', 'th'])
                    for j, cell in enumerate(cells):
                        if i < len(doc_table.rows) and j < len(doc_table.columns):
                             doc_table.cell(i, j).text = cell.get_text(strip=True)
                document.add_paragraph() # Add space after table
            except Exception as table_err:
                 print(f"Could not convert table: {table_err}")


        document.save(docx_file)
        print(f"DOCX file saved to {docx_file}")

    except FileNotFoundError:
        print(f"Error: HTML file not found at {html_file}")
    except Exception as e:
        print(f"An error occurred during HTML to DOCX conversion: {e}")


app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
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

    html_content = ""
    docx_path = ""
    html_output_filename = "output.html" # Use a fixed name or generate one
    docx_output_filename = "output.docx"

    try:
        # Perform PDF to HTML conversion using Gemini Pro with the detailed prompt
        html_output_path = pdf_to_html_with_gemini(filepath, OUTPUT_DIR)

        if html_output_path:
            with open(html_output_path, 'r', encoding='utf-8') as f:
                html_content = f.read()

            # Generate the DOCX file from the created HTML
            docx_full_path = os.path.join(OUTPUT_DIR, docx_output_filename)
            html_to_docx(html_output_path, docx_full_path)
            if os.path.exists(docx_full_path):
                 docx_path = docx_output_filename # Pass only filename for URL generation

    except Exception as overall_error:
        # Display the error in the HTML content area for feedback
        html_content = f"<p style='color:red;'>An error occurred during conversion: {overall_error}</p>"
        print(html_content)

    # Pass the HTML content and the relative path for the DOCX download link
    return render_template('html_result.html', html_content=html_content, doc_path=docx_path)

@app.route('/download/<path:filename>')
def download(filename):
    # Ensure the filename is safe (basic check)
    safe_filename = os.path.basename(filename)
    if safe_filename != filename:
        return "Invalid filename", 400
    return send_from_directory(OUTPUT_DIR, safe_filename, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)