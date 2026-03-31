#!/usr/bin/env python3
"""
Test extraction from the PDF to see what regex and AI extract.
"""
import asyncio
from app.adapters.pdf_adapter import parse_pdf
from app.services.insurance_service import insurance_service


async def test_extraction():
    # Read the PDF
    with open("test_insurance_with_email.pdf", "rb") as f:
        content = f.read()

    # Extract text
    text, metadata = parse_pdf(content)

    print("=" * 80)
    print("EXTRACTED TEXT FROM PDF:")
    print("=" * 80)
    print(text)
    print("=" * 80)
    print()

    # Test regex extraction
    print("=" * 80)
    print("REGEX EXTRACTION:")
    print("=" * 80)
    extracted = insurance_service._deterministic_extract_insurance(
        sender="",
        subject=metadata.get("title") or "",
        body=text
    )

    for key, value in extracted.items():
        print(f"{key}: {value}")

    print("=" * 80)
    print()

    # Check if AI fallback should be used
    should_use_ai = insurance_service._should_use_ai_fallback(extracted, text)
    print(f"Should use AI fallback: {should_use_ai}")

    if should_use_ai:
        print("\n" + "=" * 80)
        print("AI EXTRACTION:")
        print("=" * 80)
        from app.ai.client import AIClient
        from app.config import settings

        ai_client = AIClient(settings)
        ai_extracted = await ai_client.extract_insurance_info(
            sender="PDF Upload",
            subject="test_insurance_with_email.pdf",
            body=text
        )

        for key, value in ai_extracted.items():
            print(f"{key}: {value}")

        print("=" * 80)
        print()

        # Merge
        merged = insurance_service._merge_extracted_insurance(ai_extracted, extracted)
        print("=" * 80)
        print("MERGED RESULT:")
        print("=" * 80)
        for key, value in merged.items():
            print(f"{key}: {value}")
        print("=" * 80)


if __name__ == "__main__":
    asyncio.run(test_extraction())
