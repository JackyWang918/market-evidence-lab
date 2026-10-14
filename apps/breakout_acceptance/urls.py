from django.urls import path
from . import views, chart_views
app_name='breakout_acceptance'
urlpatterns=[
 path('',views.index,name='index'),
 path('charts/',chart_views.charts,name='charts'),
 path('charts/events/<int:pk>/',chart_views.chart_event,name='chart_event'),
 path('configure/',views.configure,name='configure'),
 path('data/',views.data,name='data'),
 path('events/<int:pk>/',views.event,name='event'),
 path('runs/<int:pk>/',views.run,name='run'),
 path('runs/<int:pk>/download/<str:kind>/',views.download,name='download'),
]
