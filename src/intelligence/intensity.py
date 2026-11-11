"""Estimativa de intensidade (Borg/faixas) — computacional e **nao calibrada**.

Este modulo produz uma **estimativa determinista de intensidade** de uma rota a
partir do **resultado fisico** (Camada 02), espelhando o preditor de RPE do
`toporide` (auditado em ``docs/adr/028-auditoria-rpe-toporide.md``). Ele **nao**
substitui — nem preenche — o contrato de previsao fisiologica
(``PhysiologicalPrediction``): a estimativa aqui e explicitamente **nao calibrada
fisiologicamente** (``calibrated = False``), **nao** e medicao e **nunca** e
apresentada como zona cardiaca real.

O que este modulo **e**:

* **deterministico** — a mesma rota produz sempre a mesma estimativa; sem
  aleatoriedade, sem estado oculto;
* **auditavel** — a formula e os coeficientes estao declarados em
  ``IntensityCoefficients`` com **origem** e **limitacao** (ver §coeficientes);
* **honesto** — RPE e faixas derivam do modelo fisico transparente; nao ha rede
  neural fisiologica nem rotulo artificial de frequencia cardiaca.

O que este modulo **nao** e:

* **nao** e previsao fisiologica calibrada (isso exige modelo calibrado com
  observacoes reais — ADR-027);
* **nao** produz zonas cardiacas (bpm). As cinco faixas (``i1``..``i5``) sao
  **faixas de intensidade** derivadas do Borg 6–20, medidas em fracao de
  **distancia** e de **duracao**.

## Coeficientes (proveniencia)

Todos os coeficientes vem do preditor ``borgRpe`` do `toporide`
(``src/domain/ml/rpeEdge.ts``), **heduristicas de engenharia locais** — a unica
parte com origem fisiologica clara e a **escala de Borg** (limites 6 e 20). Cada
coeficiente entra aqui com sua origem e limitacao declaradas:

===========================  ======  ================================  =============================
Coeficiente                  Valor   Origem (toporide)                  Limitacao
===========================  ======  ================================  =============================
Piso/teto de Borg            6/20    escala de Borg 6–20              convencao, nao calibracao
Ganho de intensidade         6.0     `rpeEdge.ts:60`                  heuristica, sem fonte citada
Referencia de normalizacao   75 kg   `rpeEdge.ts:60` (massa implicita) nao documentada no toporide
Limiar de rampa              5.0     `rpeEdge.ts:61`                  heuristica, sem fonte citada
Expoente da rampa            1.3     `rpeEdge.ts:61`                  heuristica, sem fonte citada
Peso do trafego              1.5     `rpeEdge.ts:62`                  heuristica, sem fonte citada
Peso da fadiga               1.5     `rpeEdge.ts:63`                  forma nao fiel a "Banister"
Amplitude da fadiga          1.5     `rpeEdge.ts:34`                  heuristica, sem fonte citada
Janela de trabalho           1200 s  `tuning.ts:60` (20 min em FTP)   plausivel, nao citado
Cortes de faixa              10/12/14/16  `neuralSearch.ts:361`        heuristica, sem fonte citada
Limite "verde" (Z1+Z2)       12.0    `rpeEdge.ts:21`                  heuristica, sem fonte citada
Escala de estresse de via    ver `hw_stress`  `features.ts:16-24`      escala ordinal, sem fonte
===========================  ======  ================================  =============================

**FTP:** a engine nao possui "FTP"; usa-se ``sustainable_power_w`` do
``CyclistModel`` (potencia sustentavel de travessia). FTP (limiar ~1 h) e
potencia sustentavel **nao** sao o mesmo conceito — esta e uma **limitacao
registrada** (ver ``ftp_w``).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar

from physical.models import TraversalPath
from physiology import DEFAULT_BORG_SCALE, BorgScale

#: Identificador estavel do metodo (origem: heuristica do `toporide`).
INTENSITY_MODEL_ID = "toporide-heuristic"

#: Versao do metodo de estimativa de intensidade.
INTENSITY_MODEL_VERSION = "1"

#: Definicao versionada das faixas de intensidade (derivadas do Borg 6–20).
INTENSITY_BAND_DEFINITION = "borg-intensity-bands@1"

#: Nomes das cinco faixas de intensidade (nao sao zonas cardiacas).
INTENSITY_BAND_NAMES: tuple[str, ...] = ("i1", "i2", "i3", "i4", "i5")

#: Cortes superiores (Borg) de cada faixa de intensidade: ``r <= 10 -> i1``,
#: ``r <= 12 -> i2``, ``r <= 14 -> i3``, ``r <= 16 -> i4``, senao ``i5``.
INTENSITY_BAND_EDGES: tuple[float, ...] = (10.0, 12.0, 14.0, 16.0)

#: Escala de estresse de trafego por classe de via OSM (origem: `toporide`
#: `features.ts` — `hwStress`; escala ordinal de engenharia, sem fonte citada).
_HW_STRESS: dict[str, float] = {
    "cycleway": 0.05,
    "residential": 0.25,
    "living_street": 0.25,
    "tertiary": 0.5,
    "tertiary_link": 0.5,
    "secondary": 0.75,
    "secondary_link": 0.75,
    "primary": 0.95,
    "primary_link": 0.95,
    "trunk": 1.2,
    "trunk_link": 1.2,
}

#: Estresse de trafego para classe de via desconhecida (origem: idem).
_HW_STRESS_DEFAULT = 0.4


def hw_stress(highway: str | None) -> float:
    """Estresse de trafego (ordinal) de uma classe de via OSM.

    Portado de `toporide` (`features.ts`): escala **heuristica**, sem fonte
    fisiologica. Classe ausente/desconhecida usa ``_HW_STRESS_DEFAULT``.
    """
    if highway is None:
        return _HW_STRESS_DEFAULT
    return _HW_STRESS.get(highway, _HW_STRESS_DEFAULT)


@dataclass(frozen=True, slots=True)
class IntensityCoefficients:
    """Coeficientes da estimativa de intensidade, com origem declarada.

    Todos vem do `toporide` (`rpeEdge.ts`, `tuning.ts`, `neuralSearch.ts`) e sao
    **heuristicas de engenharia** (exceto os limites da escala de Borg). Ver a
    tabela de proveniencia no topo do modulo.
    """

    #: Piso da escala de Borg (convencao: Borg 6–20).
    base_rpe: float = 6.0
    #: Ganho da intensidade (W/kg relativo ao FTP normalizado) — heuristica.
    scale_gain: float = 6.0
    #: Massa de referencia (kg) usada para normalizar o FTP — nao documentada.
    ftp_reference_kg: float = 75.0
    #: Limiar de rampa (%) a partir do qual ha termo extra — heuristica.
    grade_free_pct: float = 5.0
    #: Expoente da rampa — heuristica.
    grade_exponent: float = 1.3
    #: Peso do estresse de trafego — heuristica.
    traffic_weight: float = 1.5
    #: Peso da fadiga — heuristica.
    fatigue_weight: float = 1.5
    #: Amplitude da fadiga (forma tanh) — heuristica.
    fatigue_amplitude: float = 1.5
    #: Janela de referencia de trabalho (s) — 20 min em FTP (plausivel, nao citada).
    fatigue_window_s: float = 1200.0
    #: Sensibilidade a fadiga (perfil) — multiplica o termo de fadiga.
    fatigue_sensitivity: float = 1.0


#: Coeficientes padrao (origem: `toporide`; ver tabela de proveniencia).
DEFAULT_INTENSITY_COEFFICIENTS = IntensityCoefficients()


@dataclass(frozen=True, slots=True)
class IntensitySegment:
    """Observacao de um trecho (arco) para a estimativa de intensidade.

    Unidades explicitas. ``grade_pct`` e a **rampa de subida media** do trecho
    (ganho/distancia), ``>= 0`` — o `toporide` usa o pico instantaneo
    (``arcMaxPct``), indisponivel no ``TraversalResult`` (limitacao registrada).
    """

    distance_m: float
    duration_s: float
    rider_energy_j: float
    grade_pct: float
    traffic_stress: float

    def __post_init__(self) -> None:
        for name in ("distance_m", "duration_s", "rider_energy_j", "traffic_stress"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} deve ser finito e nao negativo ({value!r})")
        if not math.isfinite(self.grade_pct):
            raise ValueError("grade_pct deve ser finito")


@dataclass(frozen=True, slots=True)
class IntensityEstimate:
    """Estimativa de intensidade de uma rota — **nao calibrada**.

    ``calibrated`` e sempre ``False`` e ``is_measurement`` sempre ``False``: a
    estimativa e computacional e nunca uma medicao fisiologica. ``mean_rpe`` e
    ``peak_rpe`` respeitam a ``borg_scale``. As distribuicoes por faixa sao
    fracoes (somam 1 quando ha distancia/duracao positiva).
    """

    borg_scale: BorgScale
    mean_rpe: float
    peak_rpe: float
    band_distance_fraction: dict[str, float]
    band_duration_fraction: dict[str, float] | None
    distance_m: float
    duration_s: float
    method_id: str = INTENSITY_MODEL_ID
    model_version: str = INTENSITY_MODEL_VERSION
    band_definition: str = INTENSITY_BAND_DEFINITION

    #: Natureza do dado: estimativa computacional, nunca medicao.
    source: ClassVar[str] = "computational_intensity_estimate"
    #: Marcacao explicita: **nao** calibrada fisiologicamente.
    calibrated: ClassVar[bool] = False

    def __post_init__(self) -> None:
        self.borg_scale.require_valid(self.mean_rpe)
        self.borg_scale.require_valid(self.peak_rpe)
        _check_fraction_dict("band_distance_fraction", self.band_distance_fraction)
        if self.band_duration_fraction is not None:
            _check_fraction_dict(
                "band_duration_fraction", self.band_duration_fraction
            )

    @property
    def is_measurement(self) -> bool:
        """Sempre ``False``: a estimativa nao e uma medicao."""
        return False

    @property
    def green_fraction(self) -> float:
        """Fracao da distancia em faixas baixas (``i1``+``i2``, RPE <= 12)."""
        return self.band_distance_fraction.get("i1", 0.0) + self.band_distance_fraction.get(
            "i2", 0.0
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "calibrated": self.calibrated,
            "is_measurement": self.is_measurement,
            "method_id": self.method_id,
            "model_version": self.model_version,
            "band_definition": self.band_definition,
            "borg_scale": self.borg_scale.value,
            "mean_rpe": round(self.mean_rpe, 2),
            "peak_rpe": round(self.peak_rpe, 2),
            "distance_m": round(self.distance_m, 3),
            "duration_s": round(self.duration_s, 3),
            "green_fraction": round(self.green_fraction, 4),
            "band_distance_fraction": {
                name: round(value, 4)
                for name, value in self.band_distance_fraction.items()
            },
            "band_duration_fraction": (
                None
                if self.band_duration_fraction is None
                else {
                    name: round(value, 4)
                    for name, value in self.band_duration_fraction.items()
                }
            ),
        }


def _check_fraction_dict(name: str, values: dict[str, float]) -> None:
    total = 0.0
    for key, value in values.items():
        if not isinstance(key, str) or not key:
            raise ValueError(f"{name}: nome de faixa invalido ({key!r})")
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(f"{name}[{key!r}] deve ser finito e nao negativo")
        total += value
    if values and abs(total - 1.0) > 1e-6:
        raise ValueError(f"{name} deve somar 1 (recebido {total!r})")


def band_for_rpe(rpe: float) -> str:
    """Nome da faixa de intensidade para um RPE (Borg)."""
    for name, edge in zip(INTENSITY_BAND_NAMES, INTENSITY_BAND_EDGES):
        if rpe <= edge:
            return name
    return INTENSITY_BAND_NAMES[-1]


def segment_rpe(
    segment: IntensitySegment,
    *,
    mass_kg: float,
    ftp_w: float,
    accumulated_j: float = 0.0,
    borg_scale: BorgScale = DEFAULT_BORG_SCALE,
    coefficients: IntensityCoefficients = DEFAULT_INTENSITY_COEFFICIENTS,
) -> float:
    """RPE (Borg) estimado de um trecho, com a fadiga acumulada informada.

    Formula auditada em ``docs/adr/028-auditoria-rpe-toporide.md`` (§1.1);
    coeficientes em ``IntensityCoefficients`` (origem: `toporide`).

    Args:
        segment: Observacao do trecho.
        mass_kg: Massa total (ciclista + bicicleta), para W/kg.
        ftp_w: Potencia de referencia (aqui: potencia sustentavel) — ver FTP.
        accumulated_j: Trabalho ja acumulado (J) antes deste trecho (fadiga).
        borg_scale: Escala em que o RPE e expresso (padrao Borg 6–20).
        coefficients: Coeficientes (origem/limitacao registradas).

    Returns:
        RPE em Borg, dentro dos limites de ``borg_scale``.
    """
    if mass_kg <= 0.0:
        raise ValueError("mass_kg deve ser positivo")

    duration = segment.duration_s
    if duration > 0.0:
        watts_per_kg = segment.rider_energy_j / duration / mass_kg
    else:
        watts_per_kg = 0.0

    reference = max(0.5, ftp_w / coefficients.ftp_reference_kg)
    intensity_term = watts_per_kg / reference * coefficients.scale_gain

    grade = max(0.0, segment.grade_pct)
    grade_term = (
        (grade - coefficients.grade_free_pct) ** coefficients.grade_exponent
        if grade > coefficients.grade_free_pct
        else 0.0
    )

    traffic_term = segment.traffic_stress * coefficients.traffic_weight

    work_window = max(1.0, ftp_w * coefficients.fatigue_window_s)
    fatigue_state = (
        1.0
        + math.tanh(accumulated_j / work_window) * coefficients.fatigue_amplitude
    ) * coefficients.fatigue_sensitivity
    fatigue_term = (fatigue_state - 1.0) * coefficients.fatigue_weight

    rpe = (
        coefficients.base_rpe
        + intensity_term
        + grade_term
        + traffic_term
        + fatigue_term
    )
    return borg_scale.clamp(rpe)


def estimate_intensity(
    segments: Sequence[IntensitySegment],
    *,
    mass_kg: float,
    ftp_w: float,
    borg_scale: BorgScale = DEFAULT_BORG_SCALE,
    coefficients: IntensityCoefficients = DEFAULT_INTENSITY_COEFFICIENTS,
) -> IntensityEstimate:
    """Estima RPE e faixas de intensidade de uma rota (por distancia e duracao).

    Percorre os trechos acumulando fadiga (trabalho no pedivela) e distribuindo
    **distancia** e **duracao** por faixa de intensidade. Deterministico.

    Args:
        segments: Trechos da rota (na ordem de percurso).
        mass_kg: Massa total (ciclista + bicicleta).
        ftp_w: Potencia de referencia (potencia sustentavel) — ver FTP.
        borg_scale: Escala em que o RPE e expresso.
        coefficients: Coeficientes (origem/limitacao registradas).

    Returns:
        ``IntensityEstimate`` (nao calibrada). Sem trechos, RPE cai no piso da
        escala e as fracoes ficam vazias.
    """
    distance_by_band: dict[str, float] = {name: 0.0 for name in INTENSITY_BAND_NAMES}
    duration_by_band: dict[str, float] = {name: 0.0 for name in INTENSITY_BAND_NAMES}
    total_distance = 0.0
    total_duration = 0.0
    weighted_rpe = 0.0
    peak_rpe = borg_scale.min_score
    accumulated_j = 0.0

    for segment in segments:
        rpe = segment_rpe(
            segment,
            mass_kg=mass_kg,
            ftp_w=ftp_w,
            accumulated_j=accumulated_j,
            borg_scale=borg_scale,
            coefficients=coefficients,
        )
        band = band_for_rpe(rpe)
        distance_by_band[band] += segment.distance_m
        duration_by_band[band] += segment.duration_s
        total_distance += segment.distance_m
        total_duration += segment.duration_s
        weighted_rpe += rpe * segment.distance_m
        peak_rpe = max(peak_rpe, rpe)
        accumulated_j += segment.rider_energy_j

    if total_distance > 0.0:
        mean_rpe = weighted_rpe / total_distance
    elif segments:
        mean_rpe = peak_rpe
    else:
        mean_rpe = borg_scale.min_score
    mean_rpe = borg_scale.clamp(mean_rpe)
    if not segments:
        peak_rpe = borg_scale.clamp(peak_rpe)

    distance_fraction = (
        {name: value / total_distance for name, value in distance_by_band.items()}
        if total_distance > 0.0
        else {}
    )
    duration_fraction = (
        {name: value / total_duration for name, value in duration_by_band.items()}
        if total_duration > 0.0
        else None
    )

    return IntensityEstimate(
        borg_scale=borg_scale,
        mean_rpe=mean_rpe,
        peak_rpe=peak_rpe,
        band_distance_fraction=distance_fraction,
        band_duration_fraction=duration_fraction,
        distance_m=total_distance,
        duration_s=total_duration,
    )


def segments_from_traversal(
    path: TraversalPath,
    arcs: Sequence,
) -> tuple[IntensitySegment, ...]:
    """Monta os trechos de intensidade a partir de um ``TraversalPath``.

    Cada ``TraversalResult`` vira um ``IntensitySegment``: a rampa de subida do
    arco e ``elevation_gain_m/distancia`` (media, ``>= 0``) e o estresse de
    trafego vem da classe de via (``Arc.highway``). Requer ``arcs`` na **mesma**
    ordem dos resultados.
    """
    results = path.results
    if len(arcs) != len(results):
        raise ValueError(
            f"arcs ({len(arcs)}) e resultados ({len(results)}) de tamanhos diferentes"
        )
    segments: list[IntensitySegment] = []
    for arc, result in zip(arcs, results):
        distance = max(0.0, float(result.distance_m))
        grade_pct = (
            100.0 * float(arc.elevation_gain_m) / distance if distance > 0.0 else 0.0
        )
        segments.append(
            IntensitySegment(
                distance_m=distance,
                duration_s=max(0.0, float(result.time_s)),
                rider_energy_j=max(0.0, float(result.rider_energy_j)),
                grade_pct=grade_pct,
                traffic_stress=hw_stress(getattr(arc, "highway", None)),
            )
        )
    return tuple(segments)
