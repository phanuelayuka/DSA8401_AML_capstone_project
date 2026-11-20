"""Run with the real (or any) saved models:
    MODEL_DIR=/path/to/final_models ENABLED_TARGETS=ph_h2o python manage.py test
"""
import io
import os
import sys
import unittest

import numpy as np
import pandas as pd
from django.conf import settings
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIClient

MODELS_PRESENT = (settings.MODEL_DIR / "domain_model.joblib").exists()


@unittest.skipUnless(MODELS_PRESENT, "no saved models in MODEL_DIR")
@override_settings(API_KEY="test-key")
class PredictApiTests(SimpleTestCase):
    def setUp(self):
        from predictor import services
        self.services = services
        self.registry = services.get_registry()
        self.client = APIClient()
        self.client.credentials(HTTP_X_API_KEY="test-key")
        self.wavenumbers = self.registry["wavenumbers"]
        random_generator = np.random.default_rng(0)
        base_shape = np.sin(self.wavenumbers / 400.0)
        self.spectra = np.vstack([base_shape + 0.05 * random_generator.normal(size=self.wavenumbers.size) for _ in range(4)])
        # make the bridged range already a straight line so bridging changes nothing
        metadata = next(iter(self.registry["targets"].values()))["metadata"]
        for lower_limit, upper_limit in metadata["wavenumber_ranges_bridged"]:
            inside = np.flatnonzero((self.wavenumbers >= lower_limit) & (self.wavenumbers <= upper_limit))
            if inside.size:
                left, right = inside[0] - 1, inside[-1] + 1
                weights = (self.wavenumbers[inside] - self.wavenumbers[left]) / (self.wavenumbers[right] - self.wavenumbers[left])
                for row in self.spectra:
                    row[inside] = (1 - weights) * row[left] + weights * row[right]
        self.layer_ids = ["KE001-1-1T", "KE001-1-1S", "KE002-1-1T", "KE003-1-1T"]

    def upload(self, table, name="spectra.csv"):
        buffer = io.BytesIO(table.to_csv(index=False).encode())
        buffer.name = name
        return self.client.post("/predict", {"file": buffer}, format="multipart")

    def make_table(self):
        table = pd.DataFrame(self.spectra, columns=[str(value) for value in self.wavenumbers])
        table.insert(0, "layer_id", self.layer_ids)
        return table

    def test_health(self):
        response = APIClient().get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_requires_api_key(self):
        response = APIClient().post("/predict", {})
        self.assertEqual(response.status_code, 403)

    def test_prediction_matches_the_model_directly(self):
        response = self.upload(self.make_table())
        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual([row["layer_id"] for row in results], self.layer_ids)
        depth_flag = np.array([1.0 if identifier.endswith("T") else 0.0 for identifier in self.layer_ids])
        features = np.hstack([self.spectra, depth_flag[:, None]])
        for target_name, entry in self.registry["targets"].items():
            expected = np.ravel(entry["model"].predict(features))
            low, high = entry["metadata"]["physical_limits"]
            expected = np.clip(expected, low, high)
            got = [row["predictions"][target_name]["value"] for row in results]
            np.testing.assert_allclose(got, expected, atol=1e-3)
            for row in results:
                prediction = row["predictions"][target_name]
                self.assertLessEqual(prediction["lower"], prediction["value"])
                self.assertGreaterEqual(prediction["upper"], prediction["value"])

    def test_replicate_scans_are_averaged(self):
        table = pd.concat([self.make_table(), self.make_table().iloc[[0]]], ignore_index=True)
        response = self.upload(table)
        self.assertEqual(response.json()["count"], 4)

    def test_bad_depth_letter_is_rejected(self):
        table = self.make_table()
        table.loc[0, "layer_id"] = "KE001-1-1X"
        response = self.upload(table)
        self.assertEqual(response.status_code, 400)

    def test_missing_layer_id_column_is_rejected(self):
        response = self.upload(self.make_table().drop(columns="layer_id"))
        self.assertEqual(response.status_code, 400)

    def test_spectra_not_covering_the_model_range_are_rejected(self):
        table = self.make_table()
        keep = ["layer_id"] + [column for column in table.columns[1:] if float(column) < 3000]
        response = self.upload(table[keep])
        self.assertEqual(response.status_code, 400)

    def test_far_away_spectrum_is_flagged_outside_domain(self):
        table = self.make_table()
        table.iloc[0, 1:] = np.random.default_rng(1).normal(size=self.wavenumbers.size) * 5
        results = self.upload(table).json()["results"]
        self.assertFalse(results[0]["inside_spectral_domain"])
        self.assertEqual(results[0]["reliability"], "do_not_use")
