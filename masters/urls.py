from django.urls import path

from . import views

app_name = "masters"

urlpatterns = [
    path("", views.master_summary, name="summary"),
    path("<slug:resource>/", views.master_list, name="list"),
    path("<slug:resource>/new/", views.master_create, name="create"),
    path("<slug:resource>/<int:pk>/", views.master_detail, name="detail"),
    path("<slug:resource>/<int:pk>/edit/", views.master_update, name="update"),
    path("<slug:resource>/<int:pk>/delete/", views.master_delete, name="delete"),
]
