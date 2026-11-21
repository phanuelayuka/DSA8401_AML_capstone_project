from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema, OpenApiResponse, OpenApiExample

from . import services


class HealthView(APIView):
    permission_classes = []   # used by the Docker health check

    @extend_schema(
        summary="Health check",
        description="Returns `ok` when all models are loaded and ready. Used by Docker/load-balancer health probes.",
        responses={
            200: OpenApiResponse(description="Service is healthy"),
            503: OpenApiResponse(description="Models failed to load"),
        },
    )
    def get(self, request):
        try:
            registry = services.get_registry()
        except Exception as error:
            return Response({"status": "error", "detail": str(error)}, status=503)
        return Response({"status": "ok", "targets": list(registry["targets"])})


class ModelsView(APIView):
    @extend_schema(
        summary="List loaded models",
        description="Returns validation metrics (R², RMSE, RPD) and prediction-interval settings for every enabled target.",
        responses={200: OpenApiResponse(description="Model metadata keyed by target name (e.g. `ph_h2o`, `orgc`)")},
    )
    def get(self, request):
        return Response(services.model_summary())


class PredictView(APIView):
    @extend_schema(
        summary="Predict soil properties from MIR spectra",
        description=(
            "Upload a **CSV** or **XLSX** file containing MIR spectra.\n\n"
            "**Required columns:**\n"
            "- `layer_id` — sample identifier; must end in `T` (topsoil) or `S` (subsoil), e.g. `KE001T`\n"
            "- One numeric column per wavenumber (column header = wavenumber in cm⁻¹, e.g. `599.7`, `601.6`, …)\n\n"
            "Replicate scans with the same `layer_id` are averaged automatically.\n\n"
            "Each result includes a prediction value with lower/upper interval bounds, "
            "plus a `reliability` flag (`ok` or `do_not_use`) based on the spectral domain check."
        ),
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "format": "binary",
                        "description": "CSV or XLSX file (max 5000 rows by default)",
                    }
                },
                "required": ["file"],
            }
        },
        responses={
            200: OpenApiResponse(
                description="Predictions per layer",
                examples=[
                    OpenApiExample(
                        "Single layer result",
                        value={
                            "count": 1,
                            "results": [
                                {
                                    "layer_id": "KE001T",
                                    "depth_class": "topsoil",
                                    "domain_distance": 1.42,
                                    "inside_spectral_domain": True,
                                    "reliability": "ok",
                                    "predictions": {
                                        "ph_h2o": {"value": 6.3, "lower": 5.8, "upper": 6.8}
                                    },
                                }
                            ],
                            "models": {
                                "ph_h2o": {
                                    "recipe": "SNV + XGBoost",
                                    "validated_r2": 0.91,
                                    "validated_rmse": 0.38,
                                    "validated_rpd": 3.3,
                                }
                            },
                        },
                    )
                ],
            ),
            400: OpenApiResponse(description="Invalid or unreadable file"),
        },
    )
    def post(self, request):
        uploaded_file = request.FILES.get("file")
        if uploaded_file is None:
            return Response({"detail": "Send the spectra file in the 'file' field"}, status=400)
        try:
            table = services.read_spectra_table(uploaded_file)
            results = services.predict_table(table)
        except services.InputError as error:
            return Response({"detail": str(error)}, status=400)
        return Response({"count": len(results), "results": results, "models": services.model_summary()})
