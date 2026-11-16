"""Presets públicos e avaliação opcional de RPE para bicicletas manuais."""
from __future__ import annotations

from router.core.physiology.presets import MANUAL_RIDER_PRESETS, ManualRiderPreset, ManualRiderProfile
from router.core.prediction.intensity import estimate_intensity
from router.core.prediction.mechanical import RouteSegment

__all__ = ["MANUAL_RIDER_PRESETS", "ManualRiderPreset", "ManualRiderProfile"]


def estimate_manual_effort(parts, rider: ManualRiderProfile) -> dict:
    segments = [RouteSegment(part.distance_m, part.time_s, part.rider_energy_j,
                             part.mechanical_energy_j, part.ascent_m, part.descent_m)
                for part in parts]
    estimate = estimate_intensity(segments, rider=rider.to_rider_model())
    return dict(
        rider=rider.to_dict(), method_id=estimate.method_id, model_version=estimate.model_version,
        calibrated=False, is_measurement=False, is_ml=False, borg_scale=estimate.borg_scale.value,
        mean_rpe=estimate.mean_rpe, peak_rpe=estimate.peak_rpe,
        mean_power_w=estimate.mechanical.mean_power_w,
        relative_intensity=estimate.relative_intensity,
        duration_s=estimate.duration_s,
        segments=[dict(arc_id=part.arc_id, duration_s=segment.duration_s,
                       distance_m=part.distance_m,
                       grade_pct=(100 * (part.ascent_m - part.descent_m) / part.distance_m
                                  if part.distance_m > 0 and part.elevation_uncertainty is not None
                                  and not part.elevation_uncertainty.has_gap else None),
                       human_power_w=segment.mean_rider_power_w,
                       relative_intensity=segment.mean_rider_power_w / rider.reference_power_w,
                       rpe=rpe)
                  for part, segment, rpe in zip(parts, segments, estimate.segment_rpes)],
        limitations=["Referências e RPE provisórios, sem calibração individual.",
                     "Idade registrada como contexto; não altera o baseline de RPE.",
                     "Sem CP/W' estimados, não há modelo de fadiga acumulada.",
                     "Cada trecho corresponde à média de um arco da simulação."],
    )
