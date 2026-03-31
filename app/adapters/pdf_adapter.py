import logging
from io import BytesIO

logger = logging.getLogger(__name__)


def parse_pdf(content: bytes) -> tuple[str, dict]:
    """
    Extract text from PDF file.

    Args:
        content: Raw PDF file bytes

    Returns:
        tuple: (full_text, metadata)
            - full_text: Extracted text from all pages
            - metadata: Dict with pages count, title, etc.
    """
    try:
        import PyPDF2

        reader = PyPDF2.PdfReader(BytesIO(content))

        # Extract text from all pages
        text_parts = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)

        full_text = "\n".join(text_parts)

        # Extract metadata
        metadata = {
            "pages": len(reader.pages),
            "title": None,
        }

        if reader.metadata:
            metadata["title"] = reader.metadata.get("/Title")

        logger.info(
            "PDF parsed successfully: %d pages, %d chars",
            metadata["pages"],
            len(full_text)
        )

        return full_text, metadata

    except ImportError as exc:
        logger.error("PyPDF2 not installed")
        raise ValueError(
            "Το PyPDF2 δεν είναι εγκατεστημένο. Εκτελέστε: pip install PyPDF2"
        ) from exc
    except Exception as exc:
        logger.error("Error parsing PDF: %s", exc)
        raise ValueError(f"Σφάλμα κατά την ανάγνωση του PDF: {str(exc)}") from exc
