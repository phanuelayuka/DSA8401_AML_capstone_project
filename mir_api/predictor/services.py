"""Loads the saved models once and turns an uploaded spectra table into predictions.

The steps mirror the notebook: replicate scans are averaged, the bridged range is interpolated,
the spectra are put on the model's wavenumber grid, depth class comes from the last letter of layer_id,
and the domain check and prediction interval follow Step 8.
"""
import sys
import threading

import joblib
import numpy as np
import pandas as pd
from django.conf import settings


class InputError(ValueError):
    """The uploaded file cannot be used. The message is returned to the client."""


_registry = None
_registry_lock = threading.Lock()


def get_registry():
    global _registry
    if _registry is not None:
        return _registry
    with _registry_lock:
        if _registry is None:
            _registry = load_registry()
    return _registry


def load_registry():
    model_directory = settings.MODEL_DIR
    # The saved pipelines refer to classes in mir_pipeline_components.py, so that file must be importable.
    if str(model_directory) not in sys.path:
        sys.path.insert(0, str(model_directory))
    domain_path = model_directory / "domain_model.joblib"
    if not domain_path.exists():
        raise FileNotFoundError(f"domain_model.joblib not found in {model_directory}")
    from mir_pipeline_components import StandardNormalVariate   # noqa: F401  (also checks the module is present)

    registry = {"domain_model": joblib.load(domain_path), "targets": {}}
    for target_name in settings.ENABLED_TARGETS:
        model_path = model_directory / f"final_model_{target_name}.joblib"
        if not model_path.exists():
            raise FileNotFoundError(f"{model_path.name} not found in {model_directory}")
        saved = joblib.load(model_path)
        registry["targets"][target_name] = {"model": saved["model"], "metadata": saved["metadata"]}
    wavenumber_sets = {tuple(entry["metadata"]["wavenumbers_cm1"]) for entry in registry["targets"].values()}
    if len(wavenumber_sets) > 1:
        raise ValueError("The enabled models were trained on different wavenumber grids")
    registry["wavenumbers"] = np.array(next(iter(wavenumber_sets)), dtype=float)
    return registry


def read_spectra_table(uploaded_file):
    name = uploaded_file.name.lower()
    try:
        if name.endswith(".csv"):
            table = pd.read_csv(uploaded_file)
        elif name.endswith((".xlsx", ".xls")):
            table = pd.read_excel(uploaded_file)
        else:
            raise InputError("Upload a .csv or .xlsx file")
    except InputError:
        raise
    except Exception as error:
        raise InputError(f"The file could not be read: {error}")
    if "layer_id" not in table.columns:
        raise InputError("The file needs a 'layer_id' column")
    if table.shape[0] > settings.MAX_UPLOAD_ROWS:
        raise InputError(f"Too many rows ({table.shape[0]}); the limit is {settings.MAX_UPLOAD_ROWS}")
    return table


def split_spectra_and_ids(table):
    wavenumber_columns = []
    for column_name in table.columns:
        try:
            wavenumber_columns.append((float(column_name), column_name))
        except (TypeError, ValueError):
            continue
    if len(wavenumber_columns) < 50:
        raise InputError("The file needs one numeric column per wavenumber (e.g. 599.7, 601.6, ...)")
    wavenumber_columns.sort()
    wavenumbers = np.array([value for value, _ in wavenumber_columns])
    if len(np.unique(wavenumbers)) != len(wavenumbers):
        raise InputError("Duplicate wavenumber columns found")

    layer_ids = table["layer_id"].astype(str).str.strip().str.upper()
    if (layer_ids.isin(["", "NAN", "NONE"])).any():
        raise InputError("Some rows have an empty layer_id")
    spectra = table[[column for _, column in wavenumber_columns]].apply(pd.to_numeric, errors="coerce")
    spectra.columns = wavenumbers
    spectra.index = layer_ids.to_numpy()
    if spectra.isna().to_numpy().any():
        raise InputError("Spectra contain missing or non-numeric values")
    spectra = spectra.groupby(level=0, sort=False).mean()   # replicate scans are averaged
    return spectra


def depth_class_from_layer_id(layer_ids):
    suffix_to_depth = {"T": "topsoil", "S": "subsoil"}
    last_letters = pd.Series([identifier[-1] for identifier in layer_ids])
    unknown = [identifier for identifier, letter in zip(layer_ids, last_letters) if letter not in suffix_to_depth]
    if unknown:
        raise InputError(f"layer_id must end in T (topsoil) or S (subsoil). Problem rows: {unknown[:5]}")
    return last_letters.map(suffix_to_depth).to_numpy()


def put_on_model_grid(spectra, model_wavenumbers, ranges_to_bridge):
    file_wavenumbers = spectra.columns.to_numpy(dtype=float)
    if file_wavenumbers.min() > model_wavenumbers.min() or file_wavenumbers.max() < model_wavenumbers.max():
        raise InputError(f"The spectra must cover {model_wavenumbers.min():.1f} to {model_wavenumbers.max():.1f} cm-1; "
                         f"the file covers {file_wavenumbers.min():.1f} to {file_wavenumbers.max():.1f}")
    values = spectra.to_numpy(dtype=float).copy()
    for lower_limit, upper_limit in ranges_to_bridge:   # straight-line bridge, as in the notebook
        inside = np.flatnonzero((file_wavenumbers >= lower_limit) & (file_wavenumbers <= upper_limit))
        if inside.size == 0:
            continue
        left, right = inside[0] - 1, inside[-1] + 1
        if left < 0 or right >= file_wavenumbers.size:
            continue
        weights = (file_wavenumbers[inside] - file_wavenumbers[left]) / (file_wavenumbers[right] - file_wavenumbers[left])
        values[:, inside] = (1 - weights)[None, :] * values[:, [left]] + weights[None, :] * values[:, [right]]
    aligned = np.vstack([np.interp(model_wavenumbers, file_wavenumbers, row) for row in values])
    return aligned


def predict_table(table):
    registry = get_registry()
    spectra = split_spectra_and_ids(table)
    layer_ids = list(spectra.index)
    depth_classes = depth_class_from_layer_id(layer_ids)
    first_metadata = next(iter(registry["targets"].values()))["metadata"]
    aligned = put_on_model_grid(spectra, registry["wavenumbers"], first_metadata["wavenumber_ranges_bridged"])
    feature_matrix = np.hstack([aligned, (depth_classes == "topsoil").astype(float)[:, None]])

    from mir_pipeline_components import StandardNormalVariate
    domain_model = registry["domain_model"]
    snv_spectra = StandardNormalVariate().fit_transform(aligned)
    scores = domain_model["pca"].transform(snv_spectra)
    domain_distance = np.sqrt(np.sum(scores ** 2 / domain_model["score_variance"], axis=1))
    inside_domain = domain_distance <= domain_model["threshold"]

    results = []
    for row_position, layer_id in enumerate(layer_ids):
        results.append({
            "layer_id": layer_id,
            "depth_class": str(depth_classes[row_position]),
            "domain_distance": round(float(domain_distance[row_position]), 3),
            "inside_spectral_domain": bool(inside_domain[row_position]),
            "reliability": "ok" if inside_domain[row_position] else "do_not_use",
            "predictions": {},
        })
    for target_name, entry in registry["targets"].items():
        metadata = entry["metadata"]
        lower_limit, upper_limit = metadata["physical_limits"]
        raw_predictions = np.ravel(entry["model"].predict(feature_matrix))
        widths = np.array([metadata["prediction_interval_half_width_by_depth"].get(depth, metadata["prediction_interval_half_width"])
                           for depth in depth_classes])
        for row_position, result in enumerate(results):
            result["predictions"][target_name] = {
                "value": round(float(np.clip(raw_predictions[row_position], lower_limit, upper_limit)), 3),
                "lower": round(float(np.clip(raw_predictions[row_position] - widths[row_position], lower_limit, upper_limit)), 3),
                "upper": round(float(np.clip(raw_predictions[row_position] + widths[row_position], lower_limit, upper_limit)), 3),
            }
    return results


def model_summary():
    registry = get_registry()
    summary = {}
    for target_name, entry in registry["targets"].items():
        metadata = entry["metadata"]
        validation = metadata["validation"]
        summary[target_name] = {
            "recipe": f"{metadata['preprocessing']} + {metadata['algorithm']}",
            "model_version": metadata["model_version"],
            "training_samples": metadata["training_samples"],
            "validated_r2": validation["r2"],
            "validated_rmse": validation["rmse"],
            "validated_rpd": validation["rpd"],
            "prediction_interval_coverage": metadata["prediction_interval_coverage"],
            "prediction_interval_half_width_by_depth": metadata["prediction_interval_half_width_by_depth"],
            "warning": ("validated RPD below 1.4: this model has no useful predictive skill"
                        if validation["rpd"] < 1.4 else None),
        }
    return summary
