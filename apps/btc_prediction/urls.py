from django.urls import path

from . import views

app_name = "btc_prediction"
urlpatterns = [path("", views.index, name="index")]
