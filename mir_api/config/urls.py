from django.urls import path, include
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView

urlpatterns = [
    path("", include("predictor.urls")),
    # OpenAPI schema (JSON) — consumed by the UI views below
    path("schema/", SpectacularAPIView.as_view(permission_classes=[]), name="schema"),
    # Swagger UI — open this in a browser to explore and test the API
    path("docs/", SpectacularSwaggerView.as_view(url_name="schema", permission_classes=[]), name="swagger-ui"),
    # ReDoc — read-only but very readable alternative
    path("redoc/", SpectacularRedocView.as_view(url_name="schema", permission_classes=[]), name="redoc"),
]
