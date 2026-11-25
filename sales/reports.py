from io import BytesIO

from django.http import FileResponse
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def invoice_pdf_response(invoice):
    buffer = BytesIO()
    document = canvas.Canvas(buffer, pagesize=A4)
    document.setTitle(invoice.invoice_number)
    document.drawString(50, 800, 'FactoryFlow GST Invoice')
    document.drawString(50, 780, f'Invoice: {invoice.invoice_number}')
    document.drawString(50, 760, f'Customer: {invoice.sales_order.customer.name}')
    document.drawString(50, 740, f'Place of supply: {invoice.place_of_supply}')
    y = 710
    for line in invoice.lines.all():
        document.drawString(
            50, y,
            f'{line.product_name} | {line.quantity} x ₹{line.unit_price} '
            f'| Discount ₹{line.discount_amount} | ₹{line.total_price}',
        )
        y -= 18
    document.drawString(50, y - 10, f'Subtotal: ₹{invoice.subtotal}')
    document.drawString(50, y - 30, f'CGST: ₹{invoice.cgst_amount}')
    document.drawString(50, y - 50, f'SGST: ₹{invoice.sgst_amount}')
    document.drawString(50, y - 70, f'IGST: ₹{invoice.igst_amount}')
    document.drawString(50, y - 110, f'Grand total: ₹{invoice.grand_total}')
    document.save()
    buffer.seek(0)
    return FileResponse(
        buffer,
        as_attachment=True,
        filename=f'{invoice.invoice_number}.pdf',
        content_type='application/pdf',
    )


def payment_receipt_pdf_response(payment):
    buffer = BytesIO()
    document = canvas.Canvas(buffer, pagesize=A4)
    document.setTitle(f'Receipt {payment.pk}')
    document.drawString(50, 800, 'FactoryFlow Payment Receipt')
    document.drawString(50, 780, f'Receipt: PAY-{payment.pk:06d}')
    document.drawString(50, 760, f'Invoice: {payment.invoice.invoice_number}')
    document.drawString(50, 740, f'Customer: {payment.invoice.sales_order.customer.name}')
    document.drawString(50, 720, f'Amount received: ₹{payment.amount}')
    document.drawString(50, 700, f'Method: {payment.method}')
    document.save()
    buffer.seek(0)
    return FileResponse(
        buffer,
        as_attachment=True,
        filename=f'PAY-{payment.pk:06d}.pdf',
        content_type='application/pdf',
    )
