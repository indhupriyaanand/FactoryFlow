from django.urls import path

from . import views

app_name = "sales"

urlpatterns = [
    path("", views.order_list, name="order_list"),
    path("new/", views.create_order, name="create_order"),
    path("orders/<int:order_id>/", views.detail, name="detail"),
    path("approvals/", views.approval_queue, name="approval_queue"),
    path("orders/<int:order_id>/edit/", views.edit_order, name="edit_order"),
    path("orders/<int:order_id>/dispatch/", views.dispatch, name="dispatch"),
    path("orders/<int:order_id>/invoice/", views.generate_invoice, name="generate_invoice"),
    path("orders/<int:order_id>/approve/", views.approve_order, name="approve_order"),
    path("orders/<int:order_id>/reject/", views.reject_order, name="reject_order"),
    path("orders/<int:order_id>/confirm/", views.confirm_order, name="confirm_order"),
    path("orders/<int:order_id>/cancel/", views.cancel_order, name="cancel_order"),
    path("shortages/", views.shortage_list, name="shortage_list"),
    path("invoices/<int:invoice_id>/pdf/", views.invoice_pdf, name="invoice_pdf"),
    path("invoices/<int:invoice_id>/payments/", views.add_payment, name="add_payment"),
    path("collections/", views.collection_history, name="collection_history"),
    path("payments/<int:payment_id>/receipt/", views.payment_receipt, name="payment_receipt"),
    path("payments/<int:payment_id>/reverse/", views.reverse_payment_view, name="reverse_payment"),
]
