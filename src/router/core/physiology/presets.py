"""Presets absolutos provisórios para ciclistas sem medições individuais."""
from __future__ import annotations

from dataclasses import dataclass

from .rider import CapacityProfile, ParameterSource, PowerReference, ReferencePowerKind, RiderModel


@dataclass(frozen=True, slots=True)
class ManualRiderPreset:
    profile: CapacityProfile
    label: str
    reference_power_w: float


# Valores absolutos de engenharia @1. Não escalam com a massa corporal,
# não representam FTP medida. No ritmo automático, são o teto da potência.
MANUAL_RIDER_PRESETS = (
    ManualRiderPreset(CapacityProfile.SEDENTARY, "Sedentário", 75.0),
    ManualRiderPreset(CapacityProfile.BEGINNER, "Iniciante", 112.5),
    ManualRiderPreset(CapacityProfile.RECREATIONAL, "Recreativo", 150.0),
    ManualRiderPreset(CapacityProfile.TRAINED, "Treinado", 225.0),
    ManualRiderPreset(CapacityProfile.COMPETITIVE, "Competitivo", 315.0),
    ManualRiderPreset(CapacityProfile.ELITE, "Elite", 412.5),
)


@dataclass(frozen=True, slots=True)
class ManualRiderProfile:
    """Entradas mínimas do RPE estimado; peso em kg e idade opcional em anos.

    A idade é contexto declarado; o baseline de RPE atual não aplica fator
    etário. A potência humana vem da simulação, não deste preset de capacidade.
    """

    profile: CapacityProfile | str = CapacityProfile.RECREATIONAL
    mass_kg: float = 75.0
    age_years: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile", CapacityProfile(self.profile))
        self.to_rider_model()  # Reutiliza a validação existente de peso e idade.

    @property
    def preset(self) -> ManualRiderPreset:
        return next(item for item in MANUAL_RIDER_PRESETS if item.profile == self.profile)

    @property
    def reference_power_w(self) -> float:
        return self.preset.reference_power_w

    def to_rider_model(self) -> RiderModel:
        reference = PowerReference.from_absolute(
            self.reference_power_w, rider_mass_kg=self.mass_kg,
            kind=ReferencePowerKind.CAPACITY_PROFILE,
            source=ParameterSource.PROFILE_ESTIMATED,
            detail=f"manual-rider-presets@1: {self.preset.label}; referência absoluta provisória",
        )
        return RiderModel(self.mass_kg, reference, capacity_profile=CapacityProfile(self.profile),
                          age_years=self.age_years)

    def to_dict(self) -> dict:
        return dict(profile=CapacityProfile(self.profile).value, label=self.preset.label,
                    mass_kg=self.mass_kg, age_years=self.age_years,
                    reference_power_w=self.reference_power_w,
                    source="profile_estimated", preset_version="manual-rider-presets@1")
