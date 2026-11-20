# Custom scikit-learn components of the MIR calibration pipeline.
# The notebook writes this file; the Django service must import the SAME file to load the saved models.
import numpy as np
from scipy.signal import savgol_filter
from sklearn.base import BaseEstimator, TransformerMixin, clone


class IdentityTransformer(BaseEstimator, TransformerMixin):
    # Leaves the spectra unchanged (the 'raw' option).
    def fit(self, spectra_matrix, target_values=None):
        self.is_fitted_ = True   # stateless, but scikit-learn needs a fitted marker
        return self

    def transform(self, spectra_matrix):
        return np.asarray(spectra_matrix, dtype=float)


class StandardNormalVariate(BaseEstimator, TransformerMixin):
    # SNV: every spectrum is centred and scaled by its own mean and standard deviation.
    def fit(self, spectra_matrix, target_values=None):
        self.is_fitted_ = True   # stateless, but scikit-learn needs a fitted marker
        return self

    def transform(self, spectra_matrix):
        spectra_matrix = np.asarray(spectra_matrix, dtype=float)
        row_means = spectra_matrix.mean(axis=1, keepdims=True)
        row_standard_deviations = spectra_matrix.std(axis=1, ddof=1, keepdims=True)
        row_standard_deviations[row_standard_deviations == 0] = 1.0
        return (spectra_matrix - row_means) / row_standard_deviations


class MultiplicativeScatterCorrection(BaseEstimator, TransformerMixin):
    # MSC: fit learns the mean (reference) spectrum from the TRAINING spectra only.
    def fit(self, spectra_matrix, target_values=None):
        self.reference_spectrum_ = np.asarray(spectra_matrix, dtype=float).mean(axis=0)
        return self

    def transform(self, spectra_matrix):
        spectra_matrix = np.asarray(spectra_matrix, dtype=float)
        corrected = np.empty_like(spectra_matrix)
        for row_position in range(spectra_matrix.shape[0]):
            slope, intercept = np.polyfit(self.reference_spectrum_, spectra_matrix[row_position], deg=1)
            if abs(slope) < 1e-12:
                slope = 1.0
            corrected[row_position] = (spectra_matrix[row_position] - intercept) / slope
        return corrected


class SavitzkyGolayDerivative(BaseEstimator, TransformerMixin):
    # Smoothing + differentiation along the wavenumber axis (derivative_order 1 or 2).
    def __init__(self, window_length=11, polynomial_order=2, derivative_order=1):
        self.window_length = window_length
        self.polynomial_order = polynomial_order
        self.derivative_order = derivative_order

    def fit(self, spectra_matrix, target_values=None):
        self.is_fitted_ = True   # stateless, but scikit-learn needs a fitted marker
        return self

    def transform(self, spectra_matrix):
        spectra_matrix = np.asarray(spectra_matrix, dtype=float)
        window_length = min(self.window_length, spectra_matrix.shape[1] - (1 - spectra_matrix.shape[1] % 2))
        return savgol_filter(spectra_matrix, window_length=window_length, polyorder=self.polynomial_order,
                             deriv=self.derivative_order, axis=1)


class SpectraAndDepthPreprocessor(BaseEstimator, TransformerMixin):
    # Applies a spectral preprocessing to the spectral columns and (optionally) appends the depth indicator.
    # Expected input: spectral columns first, then ONE depth-indicator column (1 = topsoil, 0 = subsoil).
    def __init__(self, preprocessing=None, spectral_column_count=0, use_depth_flag=False):
        self.preprocessing = preprocessing
        self.spectral_column_count = spectral_column_count
        self.use_depth_flag = use_depth_flag

    def fit(self, feature_matrix, target_values=None):
        feature_matrix = np.asarray(feature_matrix, dtype=float)
        spectral_part = feature_matrix[:, : self.spectral_column_count]
        self.preprocessing_ = clone(self.preprocessing).fit(spectral_part)
        processed_part = self.preprocessing_.transform(spectral_part)
        # The depth column is scaled to the size of a typical processed wavelength.
        self.depth_weight_ = float(np.mean(processed_part.std(axis=0))) if self.use_depth_flag else 0.0
        return self

    def transform(self, feature_matrix):
        feature_matrix = np.asarray(feature_matrix, dtype=float)
        processed_part = self.preprocessing_.transform(feature_matrix[:, : self.spectral_column_count])
        if not self.use_depth_flag:
            return processed_part
        depth_column = feature_matrix[:, [self.spectral_column_count]] * self.depth_weight_
        return np.hstack([processed_part, depth_column])
