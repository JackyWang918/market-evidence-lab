from django.urls import path

from . import views


app_name = "daily_mover_monitor"

urlpatterns = [
    path("", views.index, name="index"),
    path("history/", views.history, name="history"),
    path("<str:symbol>/", views.detail, name="detail"),
]
