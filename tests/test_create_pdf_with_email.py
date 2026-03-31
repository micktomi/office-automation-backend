#!/usr/bin/env python3
"""
Script to create a sample insurance policy PDF with email for testing.
"""
from datetime import datetime, timedelta
from PyPDF2 import PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
import io


def create_insurance_pdf_with_email(filename: str):
    """Create a sample insurance policy PDF with email."""

    # Calculate expiry date (45 days from now - within warning threshold)
    expiry_date = datetime.now() + timedelta(days=45)

    # Create PDF in memory first
    packet = io.BytesIO()
    can = canvas.Canvas(packet, pagesize=A4)

    # Title
    can.setFont("Helvetica-Bold", 16)
    can.drawString(100, 750, "INSURANCE POLICY RENEWAL NOTICE")

    # Policy details
    can.setFont("Helvetica", 12)
    y_position = 700

    lines = [
        "",
        "Policy Number: GR-INS-2025-4321",
        "",
        "Insurance Company: Alpha Insurance SA",
        "",
        f"Policy Holder: John Papadopoulos",
        "Email: john.papadopoulos@gmail.com",
        "Phone: +30 694-1234567",
        "",
        f"Expiry Date: {expiry_date.strftime('%d/%m/%Y')}",
        f"Renewal Due: {expiry_date.strftime('%Y-%m-%d')}",
        "",
        "Policy Type: Home Insurance",
        "Coverage: Full Comprehensive",
        "Premium: EUR 450/year",
        "",
        "IMPORTANT NOTICE:",
        f"Your policy expires on {expiry_date.strftime('%d/%m/%Y')}.",
        "Please contact us to renew your coverage.",
        "",
        "For more information contact:",
        "Tel: +30 210-3456789",
        "Email: renewals@alpha-insurance.gr",
        "Web: www.alpha-insurance.gr",
    ]

    for line in lines:
        can.drawString(100, y_position, line)
        y_position -= 20

    can.save()

    # Move to the beginning of the BytesIO buffer
    packet.seek(0)

    # Write to file
    writer = PdfWriter()
    from PyPDF2 import PdfReader
    reader = PdfReader(packet)
    writer.add_page(reader.pages[0])

    with open(filename, "wb") as output_file:
        writer.write(output_file)

    print(f"✅ Created test PDF with email: {filename}")
    print(f"   Policy Number: GR-INS-2025-4321")
    print(f"   Client: John Papadopoulos")
    print(f"   Email: john.papadopoulos@gmail.com")
    print(f"   Expiry Date: {expiry_date.strftime('%Y-%m-%d')}")
    print(f"   Days until expiry: 45")


if __name__ == "__main__":
    create_insurance_pdf_with_email("test_insurance_with_email.pdf")
