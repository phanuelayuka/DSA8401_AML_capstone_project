# MIR soil prediction API

Serves the pH model (and optionally SOC) trained in the MIR calibration notebook.

## Set up
1. Copy `outputs/final_models/*` from the notebook into `models/` (see `models/README.md`).
2. Make `requirements.txt` match the notebook's versions of scikit-learn, numpy, scipy, pandas and joblib, and use the same Python minor version in the `Dockerfile`.
3. `cp .env.example .env` and set `DJANGO_SECRET_KEY`, `API_KEY` and `DJANGO_ALLOWED_HOSTS`.
4. `docker compose up --build -d`

## Endpoints
| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | loads the models; used by the Docker health check (no key needed) |
| GET | `/models` | recipe, validated accuracy and interval widths of each served model |
| POST | `/predict` | multipart upload, field `file` (.csv or .xlsx) |

Send `X-API-Key: <API_KEY>` with `/models` and `/predict`.

## Input file
- one row per scan, a `layer_id` column (last letter T = topsoil, S = subsoil)
- one numeric column per wavenumber (cm-1), covering at least the model range (about 602 to 3992)
- repeated `layer_id` rows are averaged as replicate scans

## Example
    curl -H "X-API-Key: $API_KEY" -F "file=@spectra.csv" http://localhost/predict

Each result has the prediction with a lower and upper bound (depth-specific, 90% coverage), the spectral domain distance, and `reliability`: `ok`, or `do_not_use` when the spectrum is outside the calibration domain.

## Tests
    MODEL_DIR=./models ENABLED_TARGETS=ph_h2o python manage.py test

The tests compare API output with the saved model's own predictions. They are skipped when no models are present.

## Notes
- Report the validated accuracy (pH RMSE about 0.49), not the training error.
- SOC is off by default (validated R2 about 0). If enabled, `/models` shows a warning.
- Predictions outside the calibration range of the training data are extrapolation.
