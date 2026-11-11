"""Estruturas de saida da camada fisica."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterator

from ._validation import non_negative

_EPS = 1e-6


class SimulationStatus(str, Enum):
    """Confiabilidade de um resultado fisico (ADR-025, decisao 2).

    ``VALID``       — entradas adequadas; nenhuma hipotese de terreno implicita.
    ``DEGRADED``    — resultado produzido sob hipotese ou com dados incompletos
                      (por exemplo, elevacao ausente tratada como plana).
    ``UNAVAILABLE`` — nao ha condicoes de produzir uma estimativa defensavel.
    """

    VALID = "valid"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ElevationUncertainty:
    """Incerteza altimetrica estruturada de um trecho (ADR-025, decisao 2).

    Separa a distancia por **origem** do dado de elevacao. ``observed`` conta
    apenas intervalos entre amostras sustentadas por fonte (ver
    ``OBSERVED_ELEVATION_QUALITIES``); ``interpolated``/``estimated`` contam
    intervalos preenchidos posteriormente; ``missing`` e o restante, simulado
    sob a **hipotese explicita de terreno plano** (nunca uma afirmacao de que o
    terreno e plano).

    Regra decisiva: um trecho sem elevacao nao pode ser indistinguivel de um
    trecho plano real. Aqui ele aparece em ``missing_distance_m`` e o resultado
    correspondente deve ser marcado como ``DEGRADED``.
    """

    total_distance_m: float
    missing_distance_m: float = 0.0
    interpolated_distance_m: float = 0.0
    estimated_distance_m: float = 0.0
    observed_distance_m: float = 0.0

    def __post_init__(self) -> None:
        non_negative("total_distance_m", self.total_distance_m)
        non_negative("missing_distance_m", self.missing_distance_m)
        non_negative("interpolated_distance_m", self.interpolated_distance_m)
        non_negative("estimated_distance_m", self.estimated_distance_m)
        non_negative("observed_distance_m", self.observed_distance_m)
        covered = (
            self.missing_distance_m
            + self.interpolated_distance_m
            + self.estimated_distance_m
            + self.observed_distance_m
        )
        if covered > self.total_distance_m + _EPS:
            raise ValueError(
                "ElevationUncertainty: soma das distancias por origem "
                f"({covered!r}) excede total_distance_m ({self.total_distance_m!r})"
            )

    @property
    def coverage(self) -> float | None:
        """Fracao da distancia com alguma elevacao conhecida (``None`` se vazio)."""
        if self.total_distance_m <= 0.0:
            return None
        return 1.0 - self.missing_distance_m / self.total_distance_m

    @property
    def has_gap(self) -> bool:
        """Ha distancia simulada sob hipotese de terreno plano."""
        return self.missing_distance_m > 0.0

    @property
    def assumes_flat_terrain(self) -> bool:
        """A simulacao presumiu terreno plano em algum trecho (hipotese explicita)."""
        return self.has_gap

    @property
    def is_fully_observed(self) -> bool:
        return (
            self.missing_distance_m <= 0.0
            and self.interpolated_distance_m <= 0.0
            and self.estimated_distance_m <= 0.0
        )

    @classmethod
    def fully_observed(cls, total_distance_m: float) -> "ElevationUncertainty":
        """Cobertura integral sustentada por fonte de elevacao."""
        return cls(
            total_distance_m=max(0.0, float(total_distance_m)),
            observed_distance_m=max(0.0, float(total_distance_m)),
        )

    @classmethod
    def unsupported(cls, total_distance_m: float) -> "ElevationUncertainty":
        """Sem nenhuma elevacao: distancia inteira sob hipotese de plano."""
        return cls(
            total_distance_m=max(0.0, float(total_distance_m)),
            missing_distance_m=max(0.0, float(total_distance_m)),
        )

    def combine(self, other: "ElevationUncertainty") -> "ElevationUncertainty":
        """Soma duas incertezas (trechos consecutivos)."""
        return ElevationUncertainty(
            total_distance_m=self.total_distance_m + other.total_distance_m,
            missing_distance_m=self.missing_distance_m + other.missing_distance_m,
            interpolated_distance_m=(
                self.interpolated_distance_m + other.interpolated_distance_m
            ),
            estimated_distance_m=(
                self.estimated_distance_m + other.estimated_distance_m
            ),
            observed_distance_m=self.observed_distance_m + other.observed_distance_m,
        )

    def to_dict(self) -> dict[str, float | None]:
        return {
            "total_distance_m": round(self.total_distance_m, 3),
            "missing_distance_m": round(self.missing_distance_m, 3),
            "interpolated_distance_m": round(self.interpolated_distance_m, 3),
            "estimated_distance_m": round(self.estimated_distance_m, 3),
            "observed_distance_m": round(self.observed_distance_m, 3),
            "coverage": None if self.coverage is None else round(self.coverage, 4),
        }


def classify_status(
    uncertainty: ElevationUncertainty | None,
    *,
    require_elevation: bool = False,
) -> SimulationStatus:
    """Politica de status a partir da incerteza altimetrica (ADR-025, decisao 2).

    * ``UNAVAILABLE`` se o consumidor exige elevacao e ha trecho sem suporte;
    * ``DEGRADED`` se houve hipotese de plano ou dado interpolado/estimado;
    * ``VALID`` caso contrario.
    """
    if uncertainty is None:
        return SimulationStatus.VALID
    if require_elevation and uncertainty.has_gap:
        return SimulationStatus.UNAVAILABLE
    if not uncertainty.is_fully_observed:
        return SimulationStatus.DEGRADED
    return SimulationStatus.VALID


_STATUS_RANK = {
    SimulationStatus.VALID: 0,
    SimulationStatus.DEGRADED: 1,
    SimulationStatus.UNAVAILABLE: 2,
}


def worst_status(*statuses: SimulationStatus) -> SimulationStatus:
    """Status mais restritivo entre varios trechos (pior caso)."""
    result = SimulationStatus.VALID
    for status in statuses:
        if _STATUS_RANK[status] > _STATUS_RANK[result]:
            result = status
    return result


_DEFAULT_GRAVITY_M_S2 = 9.80665


@dataclass(frozen=True, slots=True)
class EnergyBalance:
    """Balanco energetico longitudinal de um trecho (ADR-025, decisao 2).

    Identidade verificada::

        dE_k + dE_p = W_wheel - aero_loss - rolling_loss - braking_loss

    ``W_wheel`` e o trabalho **positivo** entregue a roda. As perdas
    aerodinamica (``aero_loss_j``) e de rolamento (``rolling_loss_j``) sao
    **assinadas** (um vento de cauda pode tornar ``aero_loss_j`` negativo, isto
    e, uma entrada de energia). O residuo tende a zero conforme o passo de
    integracao e refinado; ele mede a qualidade numerica, nao uma energia real.
    """

    delta_kinetic_j: float
    delta_potential_j: float
    wheel_work_j: float
    aero_loss_j: float
    rolling_loss_j: float
    braking_loss_j: float

    @property
    def lhs_j(self) -> float:
        """Variacao de energia mecanica (cinetica + potencial)."""
        return self.delta_kinetic_j + self.delta_potential_j

    @property
    def rhs_j(self) -> float:
        """Trabalho entregue menos as perdas."""
        return (
            self.wheel_work_j
            - self.aero_loss_j
            - self.rolling_loss_j
            - self.braking_loss_j
        )

    @property
    def residual_j(self) -> float:
        return self.lhs_j - self.rhs_j

    @property
    def relative_residual(self) -> float | None:
        """Residuo relativo ao trabalho na roda (``None`` se nao ha trabalho)."""
        if abs(self.wheel_work_j) <= 0.0:
            return None
        return self.residual_j / abs(self.wheel_work_j)

    def to_dict(self) -> dict[str, float | None]:
        return {
            "delta_kinetic_j": round(self.delta_kinetic_j, 4),
            "delta_potential_j": round(self.delta_potential_j, 4),
            "wheel_work_j": round(self.wheel_work_j, 4),
            "aero_loss_j": round(self.aero_loss_j, 4),
            "rolling_loss_j": round(self.rolling_loss_j, 4),
            "braking_loss_j": round(self.braking_loss_j, 4),
            "residual_j": round(self.residual_j, 4),
            "relative_residual": (
                None
                if self.relative_residual is None
                else round(self.relative_residual, 6)
            ),
        }


@dataclass(frozen=True, slots=True)
class SimulationState:
    """Estado dinamico canonico de uma simulacao (ADR-025, decisao 1).

    Este e o contrato de estado da **dinamica**. Ele nao pertence a busca e nao
    carrega posicao geografica: a posicao e determinada pelo arco e pelo
    deslocamento local do integrador.

    ``elapsed_s`` e os trabalhos sao **acumulados desde o inicio do trecho
    simulado** (nao por arco). ``speed_mps`` nunca e negativo e os trabalhos sao
    acumuladores positivos. A ausencia de estimativa (por exemplo, metabolica)
    deve ser ``None`` em outro contrato — nunca zero.

    Separacao de energia (decisao 2): ``wheel_work_j`` e o trabalho mecanico
    entregue a roda; ``rider_work_j`` e o trabalho no pedivela; ``braking_loss_j``
    e a energia dissipada por frenagem. Nenhum deles e o gasto metabolico.
    """

    elapsed_s: float = 0.0
    speed_mps: float = 0.0
    wheel_work_j: float = 0.0
    rider_work_j: float = 0.0
    braking_loss_j: float = 0.0

    def __post_init__(self) -> None:
        non_negative("elapsed_s", self.elapsed_s)
        non_negative("speed_mps", self.speed_mps)
        non_negative("wheel_work_j", self.wheel_work_j)
        non_negative("rider_work_j", self.rider_work_j)
        non_negative("braking_loss_j", self.braking_loss_j)

    @classmethod
    def from_speed(cls, speed_mps: float) -> "SimulationState":
        """Estado sem trabalho acumulado, a uma velocidade (>= 0)."""
        return cls(speed_mps=max(0.0, float(speed_mps)))

    def to_dict(self) -> dict[str, float]:
        return {
            "elapsed_s": round(self.elapsed_s, 3),
            "speed_mps": round(self.speed_mps, 3),
            "wheel_work_j": round(self.wheel_work_j, 3),
            "rider_work_j": round(self.rider_work_j, 3),
            "braking_loss_j": round(self.braking_loss_j, 3),
        }


@dataclass(frozen=True, slots=True)
class TraversalResult:
    """Resultado fisico de percorrer um arco.

    ``mechanical_energy_j`` e o trabalho aplicado na roda. ``rider_energy_j``
    e o trabalho no pedivela (roda / eficiencia). ``battery_energy_j`` fica
    ``None`` enquanto a bicicleta for convencional.
    """

    time_s: float
    distance_m: float
    ascent_m: float
    descent_m: float
    initial_speed_mps: float
    final_speed_mps: float
    mechanical_energy_j: float
    rider_energy_j: float
    battery_energy_j: float | None = None
    braking_energy_j: float = 0.0
    stalled: bool = False
    arc_id: str | None = None
    state: "SimulationState | None" = None
    elevation_uncertainty: "ElevationUncertainty | None" = None
    result_status: SimulationStatus = SimulationStatus.VALID
    aero_loss_j: float = 0.0
    rolling_loss_j: float = 0.0
    #: True se algum passo usou o piso ``MIN_SPEED_MPS`` (regime de baixa
    #: velocidade regularizado). Permite ao consumidor nao superestimar a
    #: precisao do resultado (ADR-025, decisao 6, item 2).
    used_min_speed_regularization: bool = False

    def energy_balance(
        self,
        mass_kg: float,
        *,
        gravity_m_s2: float = _DEFAULT_GRAVITY_M_S2,
    ) -> EnergyBalance:
        """Balanco energetico longitudinal do arco (ADR-025, decisao 2).

        Requer a massa total em movimento, que o resultado nao carrega.
        """
        delta_k = 0.5 * mass_kg * (
            self.final_speed_mps * self.final_speed_mps
            - self.initial_speed_mps * self.initial_speed_mps
        )
        delta_p = mass_kg * gravity_m_s2 * (self.ascent_m - self.descent_m)
        return EnergyBalance(
            delta_kinetic_j=delta_k,
            delta_potential_j=delta_p,
            wheel_work_j=self.mechanical_energy_j,
            aero_loss_j=self.aero_loss_j,
            rolling_loss_j=self.rolling_loss_j,
            braking_loss_j=self.braking_energy_j,
        )

    @property
    def average_speed_mps(self) -> float:
        if self.time_s <= 0.0:
            return 0.0
        return self.distance_m / self.time_s

    @property
    def average_speed_kmh(self) -> float:
        return 3.6 * self.average_speed_mps

    @property
    def simulation_state(self) -> SimulationState:
        """Estado dinamico apos o arco (ADR-025, decisao 1).

        Quando a travessia foi simulada a partir de um estado inicial, este e o
        estado acumulado devolvido pelo simulador. Para resultados construidos
        diretamente (sem simulacao), deriva-se um estado com acumuladores iguais
        as metricas do arco e estado inicial em repouso.
        """
        if self.state is not None:
            return self.state
        return SimulationState(
            elapsed_s=max(0.0, self.time_s),
            speed_mps=max(0.0, self.final_speed_mps),
            wheel_work_j=max(0.0, self.mechanical_energy_j),
            rider_work_j=max(0.0, self.rider_energy_j),
            braking_loss_j=max(0.0, self.braking_energy_j),
        )

    def to_metrics(self) -> "RouteMetrics":
        return RouteMetrics(
            time_s=self.time_s,
            distance_m=self.distance_m,
            ascent_m=self.ascent_m,
            descent_m=self.descent_m,
            energy_j=self.rider_energy_j,
            mechanical_energy_j=self.mechanical_energy_j,
            braking_energy_j=self.braking_energy_j,
            battery_energy_j=self.battery_energy_j,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "arc_id": self.arc_id,
            "time_s": round(self.time_s, 3),
            "distance_m": round(self.distance_m, 3),
            "ascent_m": round(self.ascent_m, 3),
            "descent_m": round(self.descent_m, 3),
            "initial_speed_mps": round(self.initial_speed_mps, 3),
            "final_speed_mps": round(self.final_speed_mps, 3),
            "average_speed_mps": round(self.average_speed_mps, 3),
            "average_speed_kmh": round(self.average_speed_kmh, 3),
            "mechanical_energy_j": round(self.mechanical_energy_j, 3),
            "rider_energy_j": round(self.rider_energy_j, 3),
            "battery_energy_j": (
                None
                if self.battery_energy_j is None
                else round(self.battery_energy_j, 3)
            ),
            "braking_energy_j": round(self.braking_energy_j, 3),
            "aero_loss_j": round(self.aero_loss_j, 3),
            "rolling_loss_j": round(self.rolling_loss_j, 3),
            "stalled": self.stalled,
            "result_status": self.result_status.value,
            "used_min_speed_regularization": self.used_min_speed_regularization,
            "elevation_uncertainty": (
                None
                if self.elevation_uncertainty is None
                else self.elevation_uncertainty.to_dict()
            ),
        }


@dataclass(frozen=True, slots=True)
class RouteMetrics:
    """Metricas acumuladas de uma rota.

    A busca Pareto pode somar ``TraversalResult`` sem conhecer as equacoes
    fisicas. ``energy_j`` e a energia do ciclista (pedivela).
    """

    time_s: float = 0.0
    distance_m: float = 0.0
    ascent_m: float = 0.0
    descent_m: float = 0.0
    energy_j: float = 0.0
    mechanical_energy_j: float = 0.0
    braking_energy_j: float = 0.0
    battery_energy_j: float | None = None
    aero_loss_j: float = 0.0
    rolling_loss_j: float = 0.0

    @property
    def average_speed_mps(self) -> float:
        if self.time_s <= 0.0:
            return 0.0
        return self.distance_m / self.time_s

    @property
    def average_speed_kmh(self) -> float:
        return 3.6 * self.average_speed_mps

    def add(self, result: TraversalResult) -> "RouteMetrics":
        battery: float | None = None
        if (
            self.battery_energy_j is not None
            and result.battery_energy_j is not None
        ):
            battery = self.battery_energy_j + result.battery_energy_j

        return RouteMetrics(
            time_s=self.time_s + result.time_s,
            distance_m=self.distance_m + result.distance_m,
            ascent_m=self.ascent_m + result.ascent_m,
            descent_m=self.descent_m + result.descent_m,
            energy_j=self.energy_j + result.rider_energy_j,
            mechanical_energy_j=self.mechanical_energy_j
            + result.mechanical_energy_j,
            braking_energy_j=self.braking_energy_j + result.braking_energy_j,
            battery_energy_j=battery,
            aero_loss_j=self.aero_loss_j + result.aero_loss_j,
            rolling_loss_j=self.rolling_loss_j + result.rolling_loss_j,
        )

    @classmethod
    def from_results(
        cls, results: list[TraversalResult] | tuple[TraversalResult, ...]
    ) -> "RouteMetrics":
        metrics = cls()
        for result in results:
            metrics = metrics.add(result)
        return metrics

    def to_dict(self) -> dict[str, object]:
        return {
            "time_s": round(self.time_s, 3),
            "distance_m": round(self.distance_m, 3),
            "ascent_m": round(self.ascent_m, 3),
            "descent_m": round(self.descent_m, 3),
            "energy_j": round(self.energy_j, 3),
            "mechanical_energy_j": round(self.mechanical_energy_j, 3),
            "braking_energy_j": round(self.braking_energy_j, 3),
            "aero_loss_j": round(self.aero_loss_j, 3),
            "rolling_loss_j": round(self.rolling_loss_j, 3),
            "battery_energy_j": (
                None
                if self.battery_energy_j is None
                else round(self.battery_energy_j, 3)
            ),
            "average_speed_mps": round(self.average_speed_mps, 3),
            "average_speed_kmh": round(self.average_speed_kmh, 3),
        }


@dataclass(frozen=True, slots=True)
class TraversalPath:
    """Resultado de percorrer arcos em continuidade.

    O modelo fisico propaga a velocidade entre arcos consecutivas: a
    velocidade final de um arco e a velocidade inicial da proxima. Por
    isso ``final_speed_mps`` e um **estado fisico de saida**, nao uma metrica
    a ser recalculada pela busca.

    A busca Pareto apenas carrega esse estado: ao expandir um label, ela
    reutiliza ``final_speed_mps`` como ``initial_speed_mps`` da proxima
    travessia. Ela nao deve reconstruir a dinamica por conta propria.

    Pensado para comportar, no futuro, outros componentes de estado (por
    exemplo ``battery_wh`` ou ``fatigue_state``) sem quebrar a interface.
    """

    results: tuple[TraversalResult, ...]
    metrics: RouteMetrics
    initial_speed_mps: float
    final_speed_mps: float
    elevation_uncertainty: "ElevationUncertainty | None" = None
    result_status: SimulationStatus = SimulationStatus.VALID
    #: True se algum arco do caminho usou o piso ``MIN_SPEED_MPS``.
    used_min_speed_regularization: bool = False

    def __iter__(self) -> Iterator[TraversalResult]:
        return iter(self.results)

    def __len__(self) -> int:
        return len(self.results)

    def __getitem__(self, index: int) -> TraversalResult:
        return self.results[index]

    @property
    def arc_ids(self) -> tuple[str | None, ...]:
        return tuple(result.arc_id for result in self.results)

    def to_dict(self) -> dict[str, object]:
        return {
            "initial_speed_mps": round(self.initial_speed_mps, 3),
            "final_speed_mps": round(self.final_speed_mps, 3),
            "arcs": len(self.results),
            "result_status": self.result_status.value,
            "used_min_speed_regularization": self.used_min_speed_regularization,
            "elevation_uncertainty": (
                None
                if self.elevation_uncertainty is None
                else self.elevation_uncertainty.to_dict()
            ),
            "metrics": self.metrics.to_dict(),
        }
