from django.urls import path

from . import views


app_name = "daily_mover_monitor"

urlpatterns = [
    path("", views.index, name="index"),
    path("history/", views.history, name="history"),
    path("structures/", views.watchlist, name="watchlist"),
    path("trends/", views.trend_list, name="trend_list"),
    path("trends/<str:symbol>/", views.trend_detail, name="trend_detail"),
    path("<str:symbol>/", views.detail, name="detail"),
]
