"""Modelo fisiológico do ciclista: capacidade, referência de potência e FC.

Este módulo separa três conceitos que não podem ser confundidos:

* **capacidade** — perfil de potência de referência por quilograma de ciclista;
* **referência de potência** — FTP medida, potência crítica medida, estimativa
  por perfil ou valor provisório, sempre com proveniência explícita;
* **frequência cardíaca** — FC máxima fisiológica e FC de repouso, com a
  distinção entre valor medido, equação populacional e indisponibilidade.

A referência de potência é **sempre** ``P_ref = massa_do_ciclista * p_ref``.
A massa do sistema (ciclista + bicicleta + bagagem) pertence à física da rota,
não a este módulo.

Nada aqui inventa FC de repouso, FTP ou potência crítica. Quando um dado não
existe, o campo fica ``None`` e a proveniência correspondente é explícita.

Referências:

* Tanaka et al. (2001), ``HRmax = 208 - 0.7 * idade`` — **equação
  populacional**, não predição individual;
* perfis de capacidade são presets de engenharia, não medidas individuais.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ._validation import finite, positive

#: Equação de Tanaka (2001) para FC máxima populacional.
TANAKA_INTERCEPT_BPM = 208.0
TANAKA_AGE_COEFFICIENT_BPM_PER_YEAR = 0.7


class CapacityProfile(str, Enum):
    """Perfis de capacidade aeróbia, em W/kg de **massa do ciclista**.

    São presets de engenharia para quando não há FTP medida. Não são uma
    medida individual nem uma previsão clínica.
    """

    SEDENTARY = "sedentary"
    BEGINNER = "beginner"
    RECREATIONAL = "recreational"
    TRAINED = "trained"
    COMPETITIVE = "competitive"
    ELITE = "elite"

    @property
    def reference_w_per_kg(self) -> float:
        """Potência de referência do perfil (W/kg de massa do ciclista)."""
        return _CAPACITY_W_PER_KG[self]

    @property
    def description(self) -> str:
        return _CAPACITY_DESCRIPTIONS[self]


_CAPACITY_W_PER_KG: dict[CapacityProfile, float] = {
    CapacityProfile.SEDENTARY: 1.0,
    CapacityProfile.BEGINNER: 1.5,
    CapacityProfile.RECREATIONAL: 2.0,
    CapacityProfile.TRAINED: 3.0,
    CapacityProfile.COMPETITIVE: 4.2,
    CapacityProfile.ELITE: 5.5,
}

_CAPACITY_DESCRIPTIONS: dict[CapacityProfile, str] = {
    CapacityProfile.SEDENTARY: "pouca ou nenhuma atividade ciclística regular",
    CapacityProfile.BEGINNER: "iniciante com atividade ocasional",
    CapacityProfile.RECREATIONAL: "ciclista recreacional regular",
    CapacityProfile.TRAINED: "treinado com volume semanal consistente",
    CapacityProfile.COMPETITIVE: "competitivo amador",
    CapacityProfile.ELITE: "alto rendimento",
}


class ActivityLevel(str, Enum):
    """Nível habitual de atividade física (seis faixas).

    O nível é um descritor do histórico habitual. Quando não há perfil de
    capacidade explícito, ele fornece um preset **provisório** de capacidade;
    quando há, o perfil explícito sempre prevalece.
    """

    SEDENTARY = "sedentary"
    LIGHTLY_ACTIVE = "lightly_active"
    MODERATELY_ACTIVE = "moderately_active"
    ACTIVE = "active"
    VERY_ACTIVE = "very_active"
    HIGHLY_ACTIVE = "highly_active"

    @property
    def weekly_active_minutes(self) -> tuple[int, int]:
        """Faixa orientativa de minutos de atividade por semana."""
        return _ACTIVITY_MINUTES[self]

    @property
    def description(self) -> str:
        low, high = self.weekly_active_minutes
        return f"{low}-{high} min/semana de atividade"


_ACTIVITY_MINUTES: dict[ActivityLevel, tuple[int, int]] = {
    ActivityLevel.SEDENTARY: (0, 29),
    ActivityLevel.LIGHTLY_ACTIVE: (30, 89),
    ActivityLevel.MODERATELY_ACTIVE: (90, 179),
    ActivityLevel.ACTIVE: (180, 359),
    ActivityLevel.VERY_ACTIVE: (360, 599),
    ActivityLevel.HIGHLY_ACTIVE: (600, 10_080),
}

#: Mapeamento **provisório** de atividade habitual para um preset de capacidade.
#: Usado apenas quando não há perfil explícito nem FTP/CP medida.
_ACTIVITY_DEFAULT_CAPACITY: dict[ActivityLevel, CapacityProfile] = {
    ActivityLevel.SEDENTARY: CapacityProfile.SEDENTARY,
    ActivityLevel.LIGHTLY_ACTIVE: CapacityProfile.BEGINNER,
    ActivityLevel.MODERATELY_ACTIVE: CapacityProfile.RECREATIONAL,
    ActivityLevel.ACTIVE: CapacityProfile.TRAINED,
    ActivityLevel.VERY_ACTIVE: CapacityProfile.COMPETITIVE,
    ActivityLevel.HIGHLY_ACTIVE: CapacityProfile.ELITE,
}

#: Perfil usado como último recurso, explicitamente marcado como provisório.
DEFAULT_CAPACITY_PROFILE = CapacityProfile.RECREATIONAL


class ParameterSource(str, Enum):
    """Proveniência de um parâmetro fisiológico."""

    #: Medido no ciclista (FTP, potência crítica, FC).
    MEASURED = "measured"
    #: Estimado a partir de um perfil/preset declarado.
    PROFILE_ESTIMATED = "profile_estimated"
    #: Estimado por um modelo calibrado com observações reais.
    CALIBRATED_MODEL_ESTIMATED = "calibrated_model_estimated"
    #: Valor provisório de engenharia, sem calibração individual.
    PROVISIONAL_DEFAULT = "provisional_default"


class ReferencePowerKind(str, Enum):
    """Natureza da potência de referência usada em ``P / P_ref``."""

    #: Limiar funcional de potência (FTP), tipicamente ~1 h.
    FTP = "ftp"
    #: Potência crítica (CP) do modelo CP/W'. **Não** é FTP.
    CRITICAL_POWER = "critical_power"
    #: Potência sustentável de travessia fornecida pelo modelo físico.
    SUSTAINABLE_POWER = "sustainable_power"
    #: Preset de capacidade em W/kg de massa do ciclista.
    CAPACITY_PROFILE = "capacity_profile"
    #: Valor provisório sem perfil nem medida.
    PROVISIONAL = "provisional"


class HeartRateMaxSource(str, Enum):
    """Origem da FC máxima fisiológica."""

    #: FC máxima medida individualmente.
    MEASURED = "measured"
    #: Equação populacional de Tanaka (2001), a partir da idade.
    TANAKA_POPULATION = "tanaka_population"
    #: Sem FC máxima disponível.
    UNAVAILABLE = "unavailable"


def tanaka_max_heart_rate(age_years: float) -> float:
    """FC máxima pela equação populacional de Tanaka (2001).

    ``HRmax = 208 - 0.7 * idade``. É uma **equação populacional**, não uma
    predição individual; o resultado deve ser rotulado como tal.
    """
    age = finite("age_years", age_years)
    if not 5.0 <= age <= 100.0:
        raise ValueError(
            f"age_years fora da faixa plausivel [5, 100] (recebido {age!r})"
        )
    return TANAKA_INTERCEPT_BPM - TANAKA_AGE_COEFFICIENT_BPM_PER_YEAR * age


@dataclass(frozen=True, slots=True)
class PowerReference:
    """Potência de referência com proveniência e tipo explícitos.

    A identidade usada no modelo é ``power_w = rider_mass_kg * w_per_kg``.
    """

    power_w: float
    w_per_kg: float
    kind: ReferencePowerKind
    source: ParameterSource
    rider_mass_kg: float
    detail: str = ""

    def __post_init__(self) -> None:
        positive("power_w", self.power_w)
        positive("w_per_kg", self.w_per_kg)
        positive("rider_mass_kg", self.rider_mass_kg)
        if not isinstance(self.kind, ReferencePowerKind):
            raise ValueError("kind deve ser um ReferencePowerKind")
        if not isinstance(self.source, ParameterSource):
            raise ValueError("source deve ser um ParameterSource")
        expected = self.rider_mass_kg * self.w_per_kg
        tolerance = 1e-6 * max(1.0, abs(self.power_w))
        if abs(self.power_w - expected) > tolerance:
            raise ValueError(
                "PowerReference inconsistente: power_w deve ser "
                f"rider_mass_kg * w_per_kg ({self.power_w!r} != {expected!r})"
            )
        if not isinstance(self.detail, str):
            raise ValueError("detail deve ser str")

    @classmethod
    def from_absolute(
        cls,
        power_w: float,
        *,
        rider_mass_kg: float,
        kind: ReferencePowerKind,
        source: ParameterSource,
        detail: str = "",
    ) -> "PowerReference":
        power = positive("power_w", power_w)
        mass = positive("rider_mass_kg", rider_mass_kg)
        return cls(
            power_w=power,
            w_per_kg=power / mass,
            kind=kind,
            source=source,
            rider_mass_kg=mass,
            detail=detail,
        )

    @classmethod
    def from_specific(
        cls,
        w_per_kg: float,
        *,
        rider_mass_kg: float,
        kind: ReferencePowerKind,
        source: ParameterSource,
        detail: str = "",
    ) -> "PowerReference":
        specific = positive("w_per_kg", w_per_kg)
        mass = positive("rider_mass_kg", rider_mass_kg)
        return cls(
            power_w=specific * mass,
            w_per_kg=specific,
            kind=kind,
            source=source,
            rider_mass_kg=mass,
            detail=detail,
        )

    @property
    def is_measured(self) -> bool:
        return self.source is ParameterSource.MEASURED

    def to_dict(self) -> dict[str, object]:
        return {
            "power_w": round(self.power_w, 3),
            "w_per_kg": round(self.w_per_kg, 4),
            "kind": self.kind.value,
            "source": self.source.value,
            "rider_mass_kg": round(self.rider_mass_kg, 3),
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class HeartRateParameters:
    """FC máxima fisiológica e FC de repouso, com origem explícita.

    ``heart_rate_reserve_bpm`` só existe quando **as duas** FCs estão
    disponíveis. A FC máxima por Tanaka é uma equação populacional; a FC de
    repouso nunca é inventada.
    """

    max_heart_rate_bpm: float | None = None
    resting_heart_rate_bpm: float | None = None
    max_source: HeartRateMaxSource = HeartRateMaxSource.UNAVAILABLE
    resting_source: ParameterSource | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.max_source, HeartRateMaxSource):
            raise ValueError("max_source deve ser um HeartRateMaxSource")
        if self.max_heart_rate_bpm is None:
            if self.max_source is not HeartRateMaxSource.UNAVAILABLE:
                raise ValueError(
                    "max_heart_rate_bpm ausente exige max_source=UNAVAILABLE"
                )
        else:
            positive("max_heart_rate_bpm", self.max_heart_rate_bpm)
            if self.max_source is HeartRateMaxSource.UNAVAILABLE:
                raise ValueError(
                    "max_heart_rate_bpm presente exige max_source explicito"
                )

        if self.resting_heart_rate_bpm is None:
            if self.resting_source is not None:
                raise ValueError(
                    "resting_source só existe quando resting_heart_rate_bpm existe"
                )
        else:
            positive("resting_heart_rate_bpm", self.resting_heart_rate_bpm)
            if self.resting_source is None:
                object.__setattr__(
                    self, "resting_source", ParameterSource.MEASURED
                )
            elif not isinstance(self.resting_source, ParameterSource):
                raise ValueError("resting_source deve ser um ParameterSource")

        if (
            self.max_heart_rate_bpm is not None
            and self.resting_heart_rate_bpm is not None
            and self.resting_heart_rate_bpm >= self.max_heart_rate_bpm
        ):
            raise ValueError(
                "resting_heart_rate_bpm deve ser menor que max_heart_rate_bpm"
            )

    @property
    def heart_rate_reserve_bpm(self) -> float | None:
        """``HRR = HRmax - FCrepouso``; ``None`` se faltar uma das duas."""
        if self.max_heart_rate_bpm is None or self.resting_heart_rate_bpm is None:
            return None
        return self.max_heart_rate_bpm - self.resting_heart_rate_bpm

    @property
    def has_absolute_bpm(self) -> bool:
        """True quando há FC máxima e de repouso (permite bpm absoluto)."""
        return self.heart_rate_reserve_bpm is not None

    @property
    def max_is_population_estimate(self) -> bool:
        return self.max_source is HeartRateMaxSource.TANAKA_POPULATION

    def to_dict(self) -> dict[str, object]:
        return {
            "max_heart_rate_bpm": (
                None
                if self.max_heart_rate_bpm is None
                else round(self.max_heart_rate_bpm, 3)
            ),
            "resting_heart_rate_bpm": (
                None
                if self.resting_heart_rate_bpm is None
                else round(self.resting_heart_rate_bpm, 3)
            ),
            "heart_rate_reserve_bpm": (
                None
                if self.heart_rate_reserve_bpm is None
                else round(self.heart_rate_reserve_bpm, 3)
            ),
            "max_source": self.max_source.value,
            "resting_source": (
                None if self.resting_source is None else self.resting_source.value
            ),
            "max_is_population_estimate": self.max_is_population_estimate,
        }


@dataclass(frozen=True, slots=True)
class RiderModel:
    """Ciclista fisiológico usado pelo modelo de intensidade.

    ``rider_mass_kg`` é a massa usada para ``P_ref``. A massa total do sistema
    (ciclista + bicicleta + bagagem) é um dado de ``RoutePhysics`` e não entra
    neste modelo.
    """

    rider_mass_kg: float
    reference: PowerReference
    capacity_profile: CapacityProfile | None = None
    activity_level: ActivityLevel | None = None
    age_years: float | None = None
    heart_rate: HeartRateParameters = field(default_factory=HeartRateParameters)
    critical_power_w: float | None = None
    w_prime_j: float | None = None

    def __post_init__(self) -> None:
        positive("rider_mass_kg", self.rider_mass_kg)
        if not isinstance(self.reference, PowerReference):
            raise ValueError("reference deve ser um PowerReference")
        if abs(self.reference.rider_mass_kg - self.rider_mass_kg) > 1e-6:
            raise ValueError(
                "reference.rider_mass_kg deve coincidir com rider_mass_kg "
                f"({self.reference.rider_mass_kg!r} != {self.rider_mass_kg!r})"
            )
        if self.capacity_profile is not None and not isinstance(
            self.capacity_profile, CapacityProfile
        ):
            raise ValueError("capacity_profile deve ser um CapacityProfile ou None")
        if self.activity_level is not None and not isinstance(
            self.activity_level, ActivityLevel
        ):
            raise ValueError("activity_level deve ser um ActivityLevel ou None")
        if self.age_years is not None:
            age = finite("age_years", self.age_years)
            if not 5.0 <= age <= 100.0:
                raise ValueError(
                    f"age_years fora da faixa plausivel [5, 100] (recebido {age!r})"
                )
        if not isinstance(self.heart_rate, HeartRateParameters):
            raise ValueError("heart_rate deve ser um HeartRateParameters")
        if self.critical_power_w is not None:
            positive("critical_power_w", self.critical_power_w)
        if self.w_prime_j is not None:
            positive("w_prime_j", self.w_prime_j)
            if self.critical_power_w is None:
                raise ValueError(
                    "w_prime_j exige critical_power_w (CP/W' andam juntos)"
                )

    @property
    def reference_power_w(self) -> float:
        return self.reference.power_w

    @property
    def reference_w_per_kg(self) -> float:
        return self.reference.w_per_kg

    @property
    def has_critical_power_model(self) -> bool:
        """True quando CP e W' estão disponíveis para o modelo de fadiga."""
        return self.critical_power_w is not None and self.w_prime_j is not None

    @property
    def heart_rate_max_bpm(self) -> float | None:
        return self.heart_rate.max_heart_rate_bpm

    @property
    def heart_rate_rest_bpm(self) -> float | None:
        return self.heart_rate.resting_heart_rate_bpm

    @property
    def heart_rate_reserve_bpm(self) -> float | None:
        return self.heart_rate.heart_rate_reserve_bpm

    def to_dict(self) -> dict[str, object]:
        return {
            "rider_mass_kg": round(self.rider_mass_kg, 3),
            "reference": self.reference.to_dict(),
            "capacity_profile": (
                None
                if self.capacity_profile is None
                else self.capacity_profile.value
            ),
            "activity_level": (
                None if self.activity_level is None else self.activity_level.value
            ),
            "age_years": None if self.age_years is None else round(self.age_years, 3),
            "heart_rate": self.heart_rate.to_dict(),
            "critical_power_w": (
                None
                if self.critical_power_w is None
                else round(self.critical_power_w, 3)
            ),
            "w_prime_j": (
                None if self.w_prime_j is None else round(self.w_prime_j, 3)
            ),
            "has_critical_power_model": self.has_critical_power_model,
        }


def default_capacity_for_activity(
    activity_level: ActivityLevel | None,
) -> CapacityProfile | None:
    """Preset de capacidade sugerido pelo nível habitual de atividade."""
    if activity_level is None:
        return None
    return _ACTIVITY_DEFAULT_CAPACITY[activity_level]


def resolve_capacity_profile(
    capacity_profile: CapacityProfile | None,
    activity_level: ActivityLevel | None,
) -> CapacityProfile | None:
    """Resolve o perfil explícito; se ausente, usa o preset da atividade."""
    if capacity_profile is not None:
        return capacity_profile
    return default_capacity_for_activity(activity_level)


def _optional_positive(name: str, value: float | None) -> float | None:
    """Trata ``None`` e ``0`` como ausencia; valores negativos sao invalidos.

    ``0`` aparece legitimamente na fisica (ex.: ``sustainable_power_w``
    indisponivel) e nao deve virar uma referencia de potencia valida.
    """
    if value is None:
        return None
    value = float(value)
    if value == 0.0:
        return None
    return positive(name, value)


def estimate_reference_power(
    rider_mass_kg: float,
    *,
    ftp_w: float | None = None,
    critical_power_w: float | None = None,
    calibrated_reference_power_w: float | None = None,
    calibrated_reference_kind: ReferencePowerKind = ReferencePowerKind.FTP,
    capacity_profile: CapacityProfile | None = None,
    activity_level: ActivityLevel | None = None,
    provisional_power_w: float | None = None,
    fallback_capacity_profile: CapacityProfile | None = DEFAULT_CAPACITY_PROFILE,
) -> PowerReference:
    """Resolve ``P_ref`` com precedência explícita e proveniência.

    Precedência:

    1. FTP **medida**;
    2. potência crítica **medida** (tipo ``critical_power``, nunca chamada FTP);
    3. referência estimada por **modelo calibrado**;
    4. perfil de capacidade explícito;
    5. preset derivado do nível habitual de atividade;
    6. potência sustentável fornecida pela física (provisória);
    7. perfil de capacidade de último recurso (provisório).
    """
    mass = positive("rider_mass_kg", rider_mass_kg)

    measured_ftp = _optional_positive("ftp_w", ftp_w)
    if measured_ftp is not None:
        return PowerReference.from_absolute(
            measured_ftp,
            rider_mass_kg=mass,
            kind=ReferencePowerKind.FTP,
            source=ParameterSource.MEASURED,
            detail="FTP medida individualmente",
        )

    measured_cp = _optional_positive("critical_power_w", critical_power_w)
    if measured_cp is not None:
        return PowerReference.from_absolute(
            measured_cp,
            rider_mass_kg=mass,
            kind=ReferencePowerKind.CRITICAL_POWER,
            source=ParameterSource.MEASURED,
            detail="potencia critica medida (CP != FTP)",
        )

    calibrated = _optional_positive(
        "calibrated_reference_power_w", calibrated_reference_power_w
    )
    if calibrated is not None:
        return PowerReference.from_absolute(
            calibrated,
            rider_mass_kg=mass,
            kind=calibrated_reference_kind,
            source=ParameterSource.CALIBRATED_MODEL_ESTIMATED,
            detail="referencia estimada por modelo calibrado",
        )

    resolved_profile = resolve_capacity_profile(capacity_profile, activity_level)
    if resolved_profile is not None:
        return PowerReference.from_specific(
            resolved_profile.reference_w_per_kg,
            rider_mass_kg=mass,
            kind=ReferencePowerKind.CAPACITY_PROFILE,
            source=ParameterSource.PROFILE_ESTIMATED,
            detail=(
                f"perfil de capacidade {resolved_profile.value} "
                f"({resolved_profile.reference_w_per_kg:g} W/kg)"
            ),
        )

    provisional = _optional_positive("provisional_power_w", provisional_power_w)
    if provisional is not None:
        return PowerReference.from_absolute(
            provisional,
            rider_mass_kg=mass,
            kind=ReferencePowerKind.SUSTAINABLE_POWER,
            source=ParameterSource.PROVISIONAL_DEFAULT,
            detail="potencia sustentavel da fisica usada como referencia provisoria",
        )

    if fallback_capacity_profile is not None:
        return PowerReference.from_specific(
            fallback_capacity_profile.reference_w_per_kg,
            rider_mass_kg=mass,
            kind=ReferencePowerKind.CAPACITY_PROFILE,
            source=ParameterSource.PROVISIONAL_DEFAULT,
            detail=(
                "perfil de capacidade de ultimo recurso "
                f"{fallback_capacity_profile.value} "
                f"({fallback_capacity_profile.reference_w_per_kg:g} W/kg)"
            ),
        )

    raise ValueError(
        "nao foi possivel resolver P_ref: informe FTP/CP, perfil/atividade "
        "ou potencia provisoria"
    )


__all__ = [
    "CapacityProfile",
    "ActivityLevel",
    "ParameterSource",
    "ReferencePowerKind",
    "HeartRateMaxSource",
    "PowerReference",
    "HeartRateParameters",
    "RiderModel",
    "DEFAULT_CAPACITY_PROFILE",
    "TANAKA_INTERCEPT_BPM",
    "TANAKA_AGE_COEFFICIENT_BPM_PER_YEAR",
    "tanaka_max_heart_rate",
    "default_capacity_for_activity",
    "resolve_capacity_profile",
    "estimate_reference_power",
]
