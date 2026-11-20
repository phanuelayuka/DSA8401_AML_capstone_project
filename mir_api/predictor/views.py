from rest_framework.response import Response
from rest_framework.views import APIView

from . import services


class HealthView(APIView):
    permission_classes = []   # used by the Docker health check

    def get(self, request):
        try:
            registry = services.get_registry()
        except Exception as error:
            return Response({"status": "error", "detail": str(error)}, status=503)
        return Response({"status": "ok", "targets": list(registry["targets"])})


class ModelsView(APIView):
    def get(self, request):
        return Response(services.model_summary())


class PredictView(APIView):
    # POST multipart: file = .csv/.xlsx with a layer_id column and one numeric column per wavenumber.
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
