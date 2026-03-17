from django.urls import path
from routing import views

urlpatterns = [
    path('route/', views.compute_route, name='compute_route'),
    path('route/<uuid:route_id>/', views.get_route_by_id, name='get_route'),
    path('route/<uuid:route_id>/pdf/', views.download_pdf, name='download_pdf'),
    path('route/<uuid:route_id>/gpx/', views.download_gpx, name='download_gpx'),
]
