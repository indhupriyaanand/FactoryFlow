from django.urls import path

from . import views

app_name = "production"

urlpatterns = [
    path("", views.production_list, name="list"),
    path("new/", views.create, name="create"),
    path("<int:order_id>/", views.detail, name="detail"),
    path("<int:order_id>/edit/", views.edit, name="edit"),
    path("<int:order_id>/cancel/", views.cancel, name="cancel"),
    path("<int:order_id>/complete/", views.complete, name="complete"),
    path("<int:order_id>/approve/", views.approve, name="approve"),
    path("damage/", views.damage_list, name="damage_list"),
    path("damage/new/<str:damage_type>/", views.damage_create, name="damage_create"),
    path("damage/<int:record_id>/edit/", views.damage_edit, name="damage_edit"),
    path("damage/<int:record_id>/void/", views.damage_void, name="damage_void"),
]
