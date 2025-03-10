import pdfplumber
from bs4 import BeautifulSoup
import requests
import os
import docx
from docx.shared import Inches

# Gemini Pro API (Replace with your actual API key)
GEMINI_API_KEY = "YOUR_GEMINI_API_KEY"  # Ensure you have an API key

# Helper function to split text into chunks suitable for the Gemini Pro API
def chunk_text(text, chunk_size=15000):
    """Splits text into chunks of approximately `chunk_size` characters, respecting sentences."""
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

def pdf_to_html_with_gemini(pdf_path, output_dir="output"):
    """
    Converts a PDF to HTML and then to a DOCX file using Gemini Pro for HTML generation, one page at a time.

    Args:
        pdf_path (str): Path to the input PDF file.
        output_dir (str, optional): Directory to store the output files. Defaults to "output".
    """

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    all_html_content = ""  # Accumulate HTML content from all pages.

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

            for chunk in text_chunks:
                try:
                    # Gemini Pro API request
                    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key=" + GEMINI_API_KEY
                    headers = {'Content-Type': 'application/json'}
                    data = {
                        "contents": [{
                            "parts": [{
                                "text": f"Convert the following text into valid and well-structured HTML code, preserving formatting as closely as possible and use CSS inline where appropriate. Do not include HTML, BODY and HEAD tags: \n\n{chunk}"
                            }]
                        }]
                    }
                    response = requests.post(url, headers=headers, json=data)
                    response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
                    gemini_output = response.json()
                    html_fragment = gemini_output['candidates'][0]['content']['parts'][0]['text']
                    page_html_content += html_fragment


                except requests.exceptions.RequestException as e:
                    print(f"Error during Gemini Pro API call on page {page_num + 1}: {e}")
                    print(f"Response content: {response.text if response else 'No response'}") # Debugging info
                    print("Skipping chunk and continuing with the next one (if any).")
                    continue # skip to the next chunk

                except KeyError as e:  # Handle potential KeyError if the response structure is unexpected
                    print(f"Error parsing Gemini Pro API response on page {page_num + 1}: {e}")
                    print(f"Raw response: {gemini_output}")
                    print("Skipping chunk and continuing with the next one (if any).")
                    continue # skip to the next chunk

                except Exception as e:
                    print(f"An unexpected error occurred on page {page_num + 1}: {e}")
                    print("Skipping chunk and continuing with the next one (if any).")
                    continue  # skip to the next chunk

            # Append the HTML content of the current page to the overall HTML content
            all_html_content += f"<div>{page_html_content}</div>"


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
    """Converts an HTML file to a DOCX file.
    This function focuses on converting textual content.
    More advanced formatting (tables, images, complex CSS) might require a
    more robust HTML to DOCX conversion library.
    """
    try:
        with open(html_file, 'r', encoding='utf-8') as f:
            html_content = f.read()

        soup = BeautifulSoup(html_content, 'html.parser')
        text = soup.get_text() #Get all of the text

        document = docx.Document()
        document.add_paragraph(text)
        document.save(docx_file)


    except FileNotFoundError:
        print(f"Error: HTML file not found at {html_file}")
    except Exception as e:
        print(f"An error occurred during HTML to DOCX conversion: {e}")


# Example usage:
if __name__ == "__main__":
    pdf_file_path = "your_pdf_file.pdf"  # Replace with your PDF file
    pdf_to_html_with_gemini(pdf_file_path)

