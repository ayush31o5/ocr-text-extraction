from flask import Flask, request, render_template, send_from_directory
from werkzeug.utils import secure_filename
from pdf2image import convert_from_path
from PIL import Image
from docx import Document
from docx.shared import Pt
import pytesseract
import os
import re

# Set the path to the Tesseract executable (update as needed for your system)
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Flask app setup
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads/'
app.config['OUTPUT_FOLDER'] = 'output/'
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'pdf'}

# Ensure required folders exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)

# Function to check allowed file extensions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

# Function to clean unwanted English terms, keeping only Devanagari text and numbers
def clean_text(text):
    hindi_pattern = r'[^\u0900-\u097F\s\d]'  # Match non-Hindi and non-numeric characters
    cleaned_text = re.sub(hindi_pattern, '', text)
    return cleaned_text.strip()

# Function to extract text from image
def extract_text_from_image(image_path, lang='hin+san'):
    try:
        with Image.open(image_path) as image:
            text = pytesseract.image_to_string(image, lang=lang, config='--psm 6')
        return clean_text(text)
    except Exception as e:
        return f"Error extracting text from image: {e}"

# Function to extract text from PDF (process one page at a time)
def extract_text_from_pdf(pdf_path, lang='hin+san'):
    try:
        poppler_path = r"C:\path\to\poppler\bin"  # Update this path for your system
        pages = convert_from_path(pdf_path, dpi=150, poppler_path=poppler_path)
        text = ""
        for page_number, page in enumerate(pages):
            page_text = pytesseract.image_to_string(page, lang=lang, config='--psm 6')
            text += f"Page {page_number + 1}:\n{clean_text(page_text)}\n"
            page.close()  # Release memory after processing each page
        return text
    except Exception as e:
        return f"Error extracting text from PDF: {e}"

# Function to save extracted text to a Word document incrementally
def save_to_word(text, output_path):
    try:
        document = Document()
        for line in text.split('\n'):
            paragraph = document.add_paragraph()
            run = paragraph.add_run(line)
            run.font.size = Pt(12)  # Adjust font size as needed
            run.font.name = 'Arial'  # Adjust font name as needed
        document.save(output_path)
    except Exception as e:
        return f"Error saving to Word document: {e}"

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return "No file part in the request."

    file = request.files['file']

    if file.filename == '':
        return "No file selected for uploading."

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)

        # Extract text based on file type
        if filename.lower().endswith(('png', 'jpg', 'jpeg')):
            extracted_text = extract_text_from_image(file_path)
        elif filename.lower().endswith('pdf'):
            extracted_text = extract_text_from_pdf(file_path)
        else:
            return "Unsupported file type."

        # Save extracted text to a Word document
        output_doc_path = os.path.join(app.config['OUTPUT_FOLDER'], f"{os.path.splitext(filename)[0]}.docx")
        save_error = save_to_word(extracted_text, output_doc_path)
        if save_error:
            return save_error

        return render_template('result.html', text=extracted_text, doc_path=f"output/{os.path.basename(output_doc_path)}")
    else:
        return "Invalid file type. Only PNG, JPG, JPEG, and PDF are allowed."

@app.route('/output/<filename>')
def download_file(filename):
    return send_from_directory(app.config['OUTPUT_FOLDER'], filename)

if __name__ == "__main__":
    app.run(debug=True)
