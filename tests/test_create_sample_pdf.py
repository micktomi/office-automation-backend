#!/usr/bin/env python3
"""
Script to create a sample insurance policy PDF for testing.
"""
from datetime import datetime, timedelta
from PyPDF2 import PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
import io


def create_insurance_pdf(filename: str):
    """Create a sample insurance policy PDF."""

    # Calculate expiry date (60 days from now - within warning threshold)
    expiry_date = datetime.now() + timedelta(days=60)

    # Create PDF in memory first
    packet = io.BytesIO()
    can = canvas.Canvas(packet, pagesize=A4)

    # Title
    can.setFont("Helvetica-Bold", 16)
    can.drawString(100, 750, "ΑΣΦΑΛΙΣΤΗΡΙΟ ΣΥΜΒΟΛΑΙΟ")

    # Policy details
    can.setFont("Helvetica", 12)
    y_position = 700

    lines = [
        "",
        "Αριθμός Συμβολαίου: AX-2024-8765",
        "",
        "Ασφαλιστική Εταιρεία: Εθνική Ασφαλιστική",
        "",
        f"Ασφαλισμένος: Μιχάλης Παπαδόπουλος",
        "Email: mixalis@example.com",
        "Τηλέφωνο: 210-1234567",
        "",
        f"Ημερομηνία Λήξης: {expiry_date.strftime('%d/%m/%Y')}",
        "",
        "Τύπος Ασφάλισης: Αυτοκινήτου",
        "Κάλυψη: Πλήρης",
        "",
        "ΣΗΜΑΝΤΙΚΟ:",
        f"Το ασφαλιστήριό σας λήγει στις {expiry_date.strftime('%d/%m/%Y')}.",
        "Παρακαλούμε επικοινωνήστε μαζί μας για την ανανέωση.",
        "",
        "Για περισσότερες πληροφορίες επικοινωνήστε:",
        "Τηλ: 210-9876543",
        "Email: info@ethniki-asfalistiki.gr",
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

    print(f"✅ Created test PDF: {filename}")
    print(f"   Policy Number: AX-2024-8765")
    print(f"   Client: Μιχάλης Παπαδόπουλος")
    print(f"   Email: mixalis@example.com")
    print(f"   Expiry Date: {expiry_date.strftime('%Y-%m-%d')}")
    print(f"   Days until expiry: 60")


if __name__ == "__main__":
    create_insurance_pdf("test_insurance_policy.pdf")
