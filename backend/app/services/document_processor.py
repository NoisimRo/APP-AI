"""Document processing service for extracting text from various formats.

Supports:
- PDF files (.pdf)
- Text files (.txt)
- Markdown files (.md)
"""

import base64
from io import BytesIO
from typing import Optional

try:
    import PyPDF2
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

from app.core.logging import get_logger

logger = get_logger(__name__)


class DocumentProcessor:
    """Service for processing and extracting text from documents."""

    def extract_text_from_pdf(self, pdf_bytes: bytes) -> str:
        """Extract text from PDF file.

        Args:
            pdf_bytes: PDF file content as bytes

        Returns:
            Extracted text content

        Raises:
            RuntimeError: If PyPDF2 is not installed
            Exception: If PDF processing fails
        """
        if not PDF_AVAILABLE:
            raise RuntimeError(
                "PyPDF2 is not installed. Install with: pip install PyPDF2"
            )

        try:
            pdf_file = BytesIO(pdf_bytes)
            pdf_reader = PyPDF2.PdfReader(pdf_file)

            text_parts = []
            for page_num, page in enumerate(pdf_reader.pages):
                try:
                    text = page.extract_text()
                    if text:
                        text_parts.append(f"[Pagina {page_num + 1}]\n{text}\n")
                except Exception as e:
                    logger.warning(
                        "pdf_page_extraction_error",
                        page=page_num,
                        error=str(e)
                    )
                    continue

            full_text = "\n".join(text_parts)

            logger.info(
                "pdf_text_extracted",
                pages=len(pdf_reader.pages),
                chars=len(full_text)
            )

            return full_text

        except Exception as e:
            logger.error("pdf_extraction_error", error=str(e))
            raise Exception(f"Eroare la procesarea PDF: {str(e)}")

    def extract_text_from_file(
        self,
        file_content: bytes,
        filename: str,
        mime_type: Optional[str] = None
    ) -> str:
        """Extract text from file based on extension or MIME type.

        Args:
            file_content: File content as bytes
            filename: Original filename (used to determine type)
            mime_type: Optional MIME type

        Returns:
            Extracted text content

        Raises:
            ValueError: If file type is not supported
        """
        # Determine file type from extension
        extension = filename.lower().split('.')[-1] if '.' in filename else ''

        logger.info(
            "extracting_text",
            filename=filename,
            extension=extension,
            mime_type=mime_type,
            size_bytes=len(file_content)
        )

        # PDF files
        if extension == 'pdf' or (mime_type and 'pdf' in mime_type):
            return self.extract_text_from_pdf(file_content)

        # Text files (.txt, .md)
        elif extension in ['txt', 'md', 'markdown']:
            try:
                # Try UTF-8 first
                text = file_content.decode('utf-8')
                logger.info("text_extracted", encoding="utf-8", chars=len(text))
                return text
            except UnicodeDecodeError:
                # Fallback to latin-1
                try:
                    text = file_content.decode('latin-1')
                    logger.warning("text_extracted_fallback", encoding="latin-1")
                    return text
                except Exception as e:
                    raise ValueError(f"Nu s-a putut decodifica fișierul text: {str(e)}")

        else:
            raise ValueError(
                f"Tip de fișier nesuportat: {extension}. "
                f"Tipuri acceptate: PDF, TXT, MD"
            )

    def extract_text_from_base64(
        self,
        base64_content: str,
        filename: str,
        mime_type: Optional[str] = None
    ) -> str:
        """Extract text from base64-encoded file.

        Args:
            base64_content: Base64-encoded file content
            filename: Original filename
            mime_type: Optional MIME type

        Returns:
            Extracted text content
        """
        try:
            # Decode base64
            file_bytes = base64.b64decode(base64_content)

            # Extract text
            return self.extract_text_from_file(file_bytes, filename, mime_type)

        except Exception as e:
            logger.error("base64_extraction_error", error=str(e))
            raise ValueError(f"Eroare la procesarea fișierului: {str(e)}")

    def clean_text(self, text: str, max_length: Optional[int] = None) -> str:
        """Clean and normalize extracted text.

        Args:
            text: Raw extracted text
            max_length: Optional maximum length (characters)

        Returns:
            Cleaned text
        """
        # Remove excessive whitespace
        lines = text.split('\n')
        cleaned_lines = []

        for line in lines:
            line = line.strip()
            if line:  # Skip empty lines
                cleaned_lines.append(line)

        cleaned_text = '\n'.join(cleaned_lines)

        # Truncate if needed
        if max_length and len(cleaned_text) > max_length:
            cleaned_text = cleaned_text[:max_length] + "\n[...text trunchiat]"
            logger.warning("text_truncated", original_length=len(text), max_length=max_length)

        return cleaned_text

    def get_text_stats(self, text: str) -> dict:
        """Get statistics about extracted text.

        Args:
            text: Extracted text

        Returns:
            Dictionary with text statistics
        """
        lines = text.split('\n')
        words = text.split()

        return {
            "characters": len(text),
            "words": len(words),
            "lines": len(lines),
            "paragraphs": len([l for l in lines if l.strip()])
        }
