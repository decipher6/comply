# app/services/extract.py
import fitz  # PyMuPDF
import io
from typing import Optional


def extract_text_from_pdf(pdf_file: bytes) -> str:
    """
    Extract text content from a PDF file using PyMuPDF.
    
    Args:
        pdf_file: PDF file bytes
        
    Returns:
        Extracted text as a string
    """
    try:
        pdf_doc = fitz.open(stream=pdf_file, filetype="pdf")
        text_parts = []
        for page_num in range(len(pdf_doc)):
            page = pdf_doc[page_num]
            page_text = page.get_text()
            if page_text:
                text_parts.append(page_text)
        pdf_doc.close()
        return "\n\n".join(text_parts)
    except Exception as e:
        raise ValueError(f"Failed to extract text from PDF: {str(e)}")
