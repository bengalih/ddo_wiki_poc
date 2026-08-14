from django.contrib import admin
from django.urls import path

from catalog import views


urlpatterns = [
    path(
        "",
        views.item_search,
        name="item_search",
    ),
    path(
        "admin/",
        admin.site.urls,
    ),
]