from django.urls import path

from apps.planner.api import views

urlpatterns = [
    path("api/health", views.health),
    path("api/geocode", views.GeocodeView.as_view()),
    path("api/plan", views.PlanView.as_view()),
    path("api/plan/<str:plan_id>/heatmap", views.HeatmapView.as_view()),
]
