from django.urls import path

from . import views

app_name = "btc_prediction"
urlpatterns = [path("", views.index, name="index")]
urlpatterns += [
    path("predictions/", views.predictions, name="predictions"),
    path("predictions/<int:sequence>/", views.prediction_detail, name="prediction_detail"),
    path("live/", views.live_index, name="live_index"),
    path("live/<int:sequence>/", views.live_detail, name="live_detail"),
]
