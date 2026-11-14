# Physical multiobjective search

Every profile runs the same `MultiObjectiveAStar` through `search_candidates`.
`planning` selects the profile's declared objectives and orchestrates assessment;
it has no profile-specific search, predefined route or terrain-only candidate.
The full supplied graph is available to search. A bbox only describes coverage
or the map of returned routes. `use_full_graph` remains accepted for legacy
callers, but no longer changes the search area.

Each expanded arc is evaluated by the traversal model at the label's actual
entry speed. Terrain, surface, bicycle, cyclist and environment affect that
transition. Time, work, elevation metrics, exit speed and data quality propagate
to the next state. Selecting fewer display routes never changes the search.

## Shared optimizations

* `PhysicalTraversal` prepares elevation profiles, spatial steps and equilibrium
  speeds. Step caches include the integration step size.
* `PhysicalTraversalModel` has a bounded, query-local exact result cache. It
  checks arc identity and invalidates on model/scenario changes; it never rounds
  speed to reuse a traversal.
* Models may certify `arc_lower_bounds`. `ReverseCostHeuristic` uses reverse
  Dijkstra on those costs, ignoring access and turn constraints (a relaxation).
  The physical model certifies profile distance, profile ascent, distance divided
  by its declared speed ceiling, and zero energy. No metabolic or net-potential
  energy estimate is assumed admissible. Custom models without this capability
  retain the geometric heuristic, and may supply a different heuristic.
* Every objective has its own minimum remaining cost; these minima need not
  belong to the same route. The vector is a lower bound, not a proposed route.
* A complete feasible arrival can prune a prefix only if it strictly dominates
  the prefix's objective lower bound. Numerical slack is conservative; equal
  cost alternatives survive. Arrivals with unavailable simulation data cannot provide incumbents.
* Terminal objective dominance is independent of arrival speed: there is no
  continuation after the destination. Intermediate dominance preserves physical
  speed classes, arrival arcs at restricted junctions, via-way history suffixes,
  and unavailable simulation state classes. Turn rules are indexed by incoming way.

## Completeness and precision

`search_mode="exact"` exhausts the active queue, with no hidden expansion or
route count cap. `max_routes` and `collapse_similar` select presentation after
the search. Explicit progressive mode and explicit search caps remain signalled.

The existing default intermediate dominance uses 0.5 m/s speed buckets. That
comparison is approximate even though every transition uses the exact stored
speed. Results expose `state_approximated`, and plans cannot claim
`optimality_proven` under that policy. `SearchConfig(exact_speed=True)` compares
only identical speeds and can be passed to the common planner as `search_config`.
Continuous speed states can make exhaustive search substantially more expensive.
Completeness remains limited to the supplied OSM/DEM data and model precision.

The objectives are unchanged. In particular, `flattest` still minimizes ascent
and distance, while applying physics during exploration. Adding work, effort or
another objective is a separate product decision; mechanical work is not a
calibrated physiological effort prediction.

## Reproduction

`scripts/benchmark_physical_search.py` takes a graph, origin and destination.
It reports graph size, snapping/search latency, expansions, generated labels,
pruned prefixes, cache hits, actual simulations, route compliance, objective
front and approximation/truncation flags. `--both-directions` reverses the OD.

`--reference` disables heuristics, incumbent pruning and result caching.
Reference comparisons must use the same graph, objectives, precision and
scenario. An explicitly budgeted but incomplete reference cannot establish
frontier equivalence. The regression suite compares completed reference and
optimized searches on speed-dependent graphs for all five profiles.

Earlier terrain-only timings are not an equivalent baseline for this engine.

## Rio Verde simulation (2026-11-14)

Graph: 15,399 nodes / 37,718 directed arcs, local OSM + DEM cache. OD:
`(-17.804347455072403, -50.92253569751691)` to
`(-17.770388956200584, -50.920569302992085)`. Flattest, 20 m integration,
75 kg cyclist, 220 W sustained power, initial speed zero, default bicycle and
environment, existing 0.5 m/s dominance buckets, no expansion/route cap.

| Direction | Objective front | Search time | Expanded | Physics calls | Cache hits | Pruned prefixes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Forward | 37 | 12.19 s | 7,435 | 13,498 | 6,063 | 4,456 |
| Reverse | 27 | 7.36 s | 4,602 | 10,194 | 1,919 | 3,815 |

Both searches exhausted the queue, without truncation, and their paths passed
the independent circulation validator. Snapping took 5.35 s / 7.91 s in these
trials; initial graph loading took 29.06 s and is separate from route calculation.
Another full planner trial (snapping, search, assessment and output) returned
all 37 forward routes in 16.09 s with the graph already loaded. Times vary
between runs and these phase measurements come from separate trials.

The recommended forward path has 5,483.496 m and 89.333 m ascent. Results remain
approximated by intermediate speed buckets (`optimality_proven=false`), and the
physical simulator's regularization/degradation flags remain in the output.
These are simulations, not a calibrated physiological effort measurement.
