from flask import Flask, request, render_template, send_from_directory
from werkzeug.utils import secure_filename
from pdf2image import convert_from_path
from PIL import Image
from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
import pytesseract
import google.generativeai as generative_ai
import os
import re

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

generative_ai.configure(
    api_key="YOUR_API_KEY"
)

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads/'
app.config['OUTPUT_FOLDER'] = 'output/'
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'pdf'}

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def extract_text_with_layout(image, lang='hin+san'):
    """
    Extract text while preserving layout information using Tesseract's HOCR output
    """
    try:
        # Get HOCR output which contains position information
        hocr_output = pytesseract.image_to_pdf_or_hocr(image, extension='hocr', lang=lang)
        
        # Get bounding box information
        boxes = pytesseract.image_to_boxes(image, lang=lang)
        
        # Get regular text
        text = pytesseract.image_to_string(image, lang=lang, config='--psm 4 --oem 1')
        
        # Get word-level position data
        word_data = pytesseract.image_to_data(image, lang=lang, output_type=pytesseract.Output.DICT)
        
        layout_info = {
            'text': text,
            'boxes': boxes,
            'word_data': word_data,
            'hocr': hocr_output
        }
        
        return layout_info
    except Exception as e:
        return f"Error extracting text and layout: {e}"

def analyze_layout(layout_info):
    """
    Analyze the layout to detect:
    - Columns
    - Indentation levels
    - Text alignment
    - Special formatting (like centered headers)
    """
    word_data = layout_info['word_data']
    
    # Detect columns by analyzing x-positions
    x_positions = word_data['left']
    potential_columns = []
    
    # Group x-positions to detect column starts
    for x in sorted(set(x_positions)):
        if len([pos for pos in x_positions if abs(pos - x) < 20]) > 5:  # Threshold for column detection
            potential_columns.append(x)
    
    # Analyze indentation patterns
    indentation_levels = {}
    for i, left in enumerate(word_data['left']):
        if word_data['conf'][i] > 60:  # Only consider high-confidence words
            line_num = word_data['line_num'][i]
            if line_num not in indentation_levels:
                indentation_levels[line_num] = left
    
    return {
        'columns': potential_columns,
        'indentation': indentation_levels
    }

def format_text_with_layout(text, layout_analysis):
    """
    Format the extracted text to match the original layout
    """
    formatted_lines = []
    current_line = ""
    current_indent = 0
    
    for line in text.split('\n'):
        # Remove extra spaces while preserving indentation
        stripped_line = line.strip()
        if not stripped_line:
            formatted_lines.append('')
            continue
            
        # Detect if line is part of a column
        indent_match = re.match(r'^(\s*)', line)
        if indent_match:
            current_indent = len(indent_match.group(1))
        
        # Handle special cases like centered text or multi-column layout
        if any(stripped_line.startswith(str(num)) for num in range(10)):
            # This might be a page number or index entry
            current_line = ' ' * current_indent + stripped_line
        else:
            current_line = ' ' * current_indent + stripped_line
        
        formatted_lines.append(current_line)
    
    return '\n'.join(formatted_lines)

def save_to_word_with_layout(formatted_text, output_path):
    """
    Save the formatted text to a Word document while preserving layout
    """
    doc = Document()
    
    # Set up the document formatting
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.left_margin = Inches(1)
    section.right_margin = Inches(1)
    
    for line in formatted_text.split('\n'):
        # Detect indentation level
        indent_match = re.match(r'^(\s*)', line)
        indent_level = len(indent_match.group(1)) if indent_match else 0
        
        # Create paragraph with preserved formatting
        paragraph = doc.add_paragraph()
        paragraph.style = doc.styles['Normal']
        
        # Set indentation
        paragraph_format = paragraph.paragraph_format
        paragraph_format.left_indent = Inches(indent_level * 0.25)
        
        # Add the text with proper font
        run = paragraph.add_run(line.strip())
        run.font.name = 'Arial Unicode MS'
        run.font.size = Pt(11)
        
        # Preserve special formatting
        if line.strip().isupper():
            run.bold = True
        
        # Add minimal spacing between paragraphs
        paragraph_format.space_before = Pt(0)
        paragraph_format.space_after = Pt(0)
    
    doc.save(output_path)

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

        if filename.lower().endswith('pdf'):
            images = convert_from_path(file_path)
            combined_text = ""
            
            for i, image in enumerate(images):
                layout_info = extract_text_with_layout(image)
                layout_analysis = analyze_layout(layout_info)
                formatted_text = format_text_with_layout(layout_info['text'], layout_analysis)
                combined_text += f"\n--- Page {i+1} ---\n{formatted_text}\n"
        else:
            image = Image.open(file_path)
            layout_info = extract_text_with_layout(image)
            layout_analysis = analyze_layout(layout_info)
            combined_text = format_text_with_layout(layout_info['text'], layout_analysis)

        output_doc_path = os.path.join(app.config['OUTPUT_FOLDER'], f"{os.path.splitext(filename)[0]}_formatted.docx")
        save_to_word_with_layout(combined_text, output_doc_path)

        return render_template('result.html', text=combined_text, doc_path=f"output/{os.path.basename(output_doc_path)}")
    
    return "Invalid file type. Only PNG, JPG, JPEG, and PDF are allowed."

@app.route('/output/<filename>')
def download_file(filename):
    return send_from_directory(app.config['OUTPUT_FOLDER'], filename)

if __name__ == "__main__":
    app.run(debug=True)