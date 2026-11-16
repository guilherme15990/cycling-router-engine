"""Projeção da geometria e do RPE nativos para as camadas do mapa."""
from effort_colors import band_for_rpe


def mobility_segments(route):
    """Associa cada RPE à geometria do mesmo arco, inclusive arcos do snapping.

    Resultados antigos sem geometrias por arco usam a média da rota, rotulada
    explicitamente, sem tentar inferir fronteiras dos arcos pela posição.
    """
    estimate = route.effort_estimate or {}
    rows = estimate.get("segments") or []

    def segment(points, rpe, source, grade_pct=None):
        return dict(pts=[[round(float(lat), 6), round(float(lon), 6)] for lat, lon in points],
                    rpe=rpe, band=None if rpe is None else band_for_rpe(rpe),
                    grade_pct=grade_pct, rpe_source=source)

    if rows and all(len(row.get("geometry") or []) >= 2 for row in rows):
        return [segment(row["geometry"], row.get("rpe"), "segment", row.get("grade_pct")) for row in rows]
    rpe = estimate.get("mean_rpe")
    source = "route_mean" if rpe is not None else "unavailable"
    return [segment([a, b], rpe, source) for a, b in zip(route.geometry, route.geometry[1:])]
