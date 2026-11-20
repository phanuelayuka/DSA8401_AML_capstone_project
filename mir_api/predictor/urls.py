from django.urls import path
from . import views

urlpatterns = [
    path("health", views.HealthView.as_view()),
    path("models", views.ModelsView.as_view()),
    path("predict", views.PredictView.as_view()),
]
