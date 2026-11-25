from django.urls import path

from . import views

app_name = "purchases"

urlpatterns = [
    path("", views.purchase_list, name="list"),
    path("new/", views.purchase_create, name="create"),
    path("<int:invoice_id>/", views.purchase_detail, name="detail"),
    path("<int:invoice_id>/edit/", views.purchase_update, name="update"),
    path("<int:invoice_id>/receive/", views.receive, name="receive"),
    path("<int:invoice_id>/cancel/", views.cancel, name="cancel"),
]
