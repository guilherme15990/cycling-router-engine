"""Rede pequena que aproxima os objetivos físicos do perfil, sem rótulos humanos."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import random

FEATURE_NAMES = ("log_distance_m", "ascent_per_m", "descent_per_m", "entry_speed_mps",
                 "rolling_loss_j_per_m", "aero_loss_j_per_m", "elevation_coverage")
NETWORK_SCHEMA = "route-network@1"


def route_features(part) -> tuple[float, ...]:
    distance = max(part.distance_m, 1e-9)
    coverage = part.elevation_uncertainty.coverage if part.elevation_uncertainty is not None else None
    return (math.log1p(part.distance_m), part.ascent_m / distance, part.descent_m / distance,
            part.initial_speed_mps, part.rolling_loss_j / distance, part.aero_loss_j / distance,
            0.0 if coverage is None else coverage)


@dataclass(frozen=True, slots=True)
class NeuralRouteModel:
    profile: str
    objectives: tuple[str, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    target_means: tuple[float, ...]
    target_scales: tuple[float, ...]
    hidden_weights: tuple[tuple[float, ...], ...]
    hidden_bias: tuple[float, ...]
    output_weights: tuple[tuple[float, ...], ...]
    output_bias: tuple[float, ...]
    training_samples: int
    validation_samples: int
    validation_mse: float | None
    schema: str = NETWORK_SCHEMA

    def __post_init__(self):
        n, h, o = len(FEATURE_NAMES), len(self.hidden_bias), len(self.objectives)
        if (self.schema != NETWORK_SCHEMA or not h or not o or len(set(self.objectives)) != o
                or len(self.means) != n or len(self.scales) != n
                or len(self.target_means) != o or len(self.target_scales) != o
                or len(self.hidden_weights) != h or any(len(row) != n for row in self.hidden_weights)
                or len(self.output_weights) != o or any(len(row) != h for row in self.output_weights)
                or len(self.output_bias) != o or self.training_samples < 1 or self.validation_samples < 0):
            raise ValueError("artefato de rede de rotas incompatível")
        values = (*self.means, *self.scales, *self.target_means, *self.target_scales,
                  *self.hidden_bias, *self.output_bias,
                  *(value for row in (*self.hidden_weights, *self.output_weights) for value in row))
        if any(not math.isfinite(value) for value in values) or any(value < 0 for value in (*self.scales, *self.target_scales)):
            raise ValueError("parâmetros da rede devem ser finitos e escalas não negativas")
        if self.validation_mse is not None and (not math.isfinite(self.validation_mse) or self.validation_mse < 0):
            raise ValueError("validation_mse inválido")

    @property
    def model_version(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return "route-ml-" + hashlib.sha256(payload.encode()).hexdigest()[:16]

    def predict(self, features) -> tuple[float, ...]:
        if len(features) != len(FEATURE_NAMES) or any(not math.isfinite(value) for value in features):
            raise ValueError("features da rede inválidas")
        x = [(value - mean) / scale if scale > 1e-12 else 0.0
             for value, mean, scale in zip(features, self.means, self.scales)]
        hidden = [math.tanh(bias + sum(weight * value for weight, value in zip(row, x)))
                  for row, bias in zip(self.hidden_weights, self.hidden_bias)]
        return tuple(max(0.0, mean + scale * (bias + sum(weight * value for weight, value in zip(row, hidden))))
                     for row, bias, mean, scale in zip(self.output_weights, self.output_bias,
                                                     self.target_means, self.target_scales))

    def feature_influence(self) -> dict[str, float]:
        # Força dos caminhos |entrada→oculta| × |oculta→saída|, em entradas
        # padronizadas. É explicação estrutural, não causalidade nem custo.
        scores = [sum(abs(row[index]) * sum(abs(out[j]) for out, scale in
                    zip(self.output_weights, self.target_scales) if scale > 1e-12)
                    for j, row in enumerate(self.hidden_weights)) if self.scales[index] > 1e-12 else 0.0
                  for index in range(len(FEATURE_NAMES))]
        total = sum(scores)
        return {name: 100 * value / total if total else 0.0 for name, value in zip(FEATURE_NAMES, scores)}

    def to_dict(self) -> dict:
        values = asdict(self)
        for key, value in values.items():
            if isinstance(value, tuple):
                values[key] = [list(row) for row in value] if value and isinstance(value[0], tuple) else list(value)
        return values

    @classmethod
    def from_dict(cls, payload: dict) -> NeuralRouteModel:
        values = dict(payload)
        for key in ("objectives", "means", "scales", "target_means", "target_scales", "hidden_bias", "output_bias"):
            values[key] = tuple(values[key])
        for key in ("hidden_weights", "output_weights"):
            values[key] = tuple(tuple(row) for row in values[key])
        return cls(**values)


def train_route_network(profile: str, objectives: tuple[str, ...], samples, *, check=lambda: None) -> NeuralRouteModel:
    """Amostras (arc_id, features, objetivos/metro); holdout por arco, sem vazamento entre velocidades."""
    if not samples:
        raise ValueError("sem amostras físicas viáveis para treinar a rede de rotas")
    arc_ids = list(dict.fromkeys(sample[0] for sample in samples))
    held_out = set(arc_ids[::5]) if len(arc_ids) >= 5 else set()
    training = [sample for sample in samples if sample[0] not in held_out]
    validation = [sample for sample in samples if sample[0] in held_out]
    xs, ys = [sample[1] for sample in training], [sample[2] for sample in training]

    def stats(rows):
        means = tuple(sum(column) / len(column) for column in zip(*rows))
        scales = tuple(math.sqrt(sum((row[i] - mean) ** 2 for row in rows) / len(rows))
                       for i, mean in enumerate(means))
        return means, scales

    means, scales = stats(xs)
    target_means, target_scales = stats(ys)
    xrows = [[(v - m) / s if s > 1e-12 else 0.0 for v, m, s in zip(row, means, scales)] for row in xs]
    yrows = [[(v - m) / s if s > 1e-12 else 0.0 for v, m, s in zip(row, target_means, target_scales)] for row in ys]
    rng = random.Random(42)
    n, h, o = len(FEATURE_NAMES), 8, len(objectives)
    w1 = [[rng.uniform(-.2, .2) if scales[i] > 1e-12 else 0.0 for i in range(n)] for _ in range(h)]
    b1 = [0.0] * h
    w2 = [[rng.uniform(-.2, .2) if target_scales[k] > 1e-12 else 0.0 for _ in range(h)] for k in range(o)]
    b2 = [0.0] * o
    for _ in range(180):
        check()
        g1, gb1, g2, gb2 = [[0.0] * n for _ in range(h)], [0.0] * h, [[0.0] * h for _ in range(o)], [0.0] * o
        for x, y in zip(xrows, yrows):
            a = [math.tanh(bias + sum(w * v for w, v in zip(row, x))) for row, bias in zip(w1, b1)]
            errors = [max(-5.0, min(5.0, bias + sum(w * v for w, v in zip(row, a)) - target))
                      if target_scales[k] > 1e-12 else 0.0
                      for k, (row, bias, target) in enumerate(zip(w2, b2, y))]
            for k, error in enumerate(errors):
                gb2[k] += error
                for j in range(h):
                    g2[k][j] += error * a[j]
            for j in range(h):
                delta = sum(errors[k] * w2[k][j] for k in range(o)) * (1 - a[j] ** 2)
                gb1[j] += delta
                for i in range(n):
                    g1[j][i] += delta * x[i]
        rate = .04 / len(xrows)
        for j in range(h):
            b1[j] -= rate * gb1[j]
            for i in range(n):
                w1[j][i] -= rate * g1[j][i]
        for k in range(o):
            b2[k] -= rate * gb2[k]
            for j in range(h):
                w2[k][j] -= rate * g2[k][j]
    model = NeuralRouteModel(profile, objectives, means, scales, target_means, target_scales,
                             tuple(map(tuple, w1)), tuple(b1), tuple(map(tuple, w2)), tuple(b2),
                             len(training), len(validation), None)
    if validation:
        error = sum(((prediction - target) / scale) ** 2
                    for _, x, y in validation
                    for prediction, target, scale in zip(model.predict(x), y, target_scales) if scale > 1e-12)
        from dataclasses import replace
        active = sum(scale > 1e-12 for scale in target_scales)
        model = replace(model, validation_mse=error / max(1, len(validation) * active))
    return model
