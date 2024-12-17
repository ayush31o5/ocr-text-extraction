from flask import Flask, request, render_template, send_from_directory
from werkzeug.utils import secure_filename
from pdf2image import convert_from_path
from PIL import Image
from docx import Document
import pytesseract
import os

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

# Function to process an image and extract text using Tesseract OCR
def image_to_text(image_path):
    try:
        # Use Tesseract OCR to extract text from the image
        extracted_text = pytesseract.image_to_string(Image.open(image_path), lang='hin+eng')
        return extracted_text.strip() if extracted_text.strip() else "No text detected in the image."
    except Exception as e:
        return f"Error processing the image: {e}"

# Function to process a PDF and extract text using Tesseract OCR
def pdf_to_text(pdf_path):
    try:
        text = ""
        # Convert PDF pages to images
        poppler_path = None  # Set this if needed: r"C:/path/to/poppler/bin"
        pages = convert_from_path(pdf_path, dpi=150, poppler_path=poppler_path)
        for page_number, page_image in enumerate(pages):
            # Save page image temporarily
            temp_image_path = f"temp_page_{page_number}.png"
            page_image.save(temp_image_path, "PNG")

            # Extract text from the saved image
            page_text = image_to_text(temp_image_path)
            text += f"Page {page_number + 1}:\n{page_text}\n"

            # Clean up the temporary image
            os.remove(temp_image_path)

        return text if text.strip() else "No text detected in the PDF."
    except Exception as e:
        return f"Error processing the PDF: {e}"

# Function to save extracted text to a Word document
def save_to_word(text, output_path):
    try:
        document = Document()
        document.add_paragraph(text)
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
            extracted_text = image_to_text(file_path)
        elif filename.lower().endswith('pdf'):
            extracted_text = pdf_to_text(file_path)
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
