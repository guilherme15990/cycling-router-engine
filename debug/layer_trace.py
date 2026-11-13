"""Observação da chamada real da API, restrita à thread do Streamlit."""
from __future__ import annotations

import dataclasses
import enum
import inspect
import itertools
import sys
import threading
import time
from collections import Counter
from pathlib import Path

LAYERS = {
    "01": "Dados, grafo, snapping e bbox",
    "02": "Modelo físico e cálculos matemáticos",
    "03": "Perfis, custo e busca",
    "04": "ML, intensidade e predição",
    "05": "Avaliação, classificação e seleção",
    "06": "Contrato e resultado",
}


def snapshot(value, depth=0, _budget=None):
    """Snapshot limitado; não invoca serializers ou funções do motor."""
    if _budget is None:
        _budget = [300]
    _budget[0] -= 1
    if _budget[0] < 0:
        return "… limite da amostra atingido"
    if isinstance(value, enum.Enum):
        return value.value
    if value is None or isinstance(value, (bool, int, float, str)):
        return value[:1500] if isinstance(value, str) else value
    if isinstance(value, Path):
        return str(value)
    if hasattr(type(value), "node_count") and hasattr(type(value), "arc_count"):
        return {
            "type": type(value).__name__,
            "nodes": value.node_count,
            "arcs": value.arc_count,
            "spatial_index": (
                type(getattr(value, "_node_spatial_index", None)).__name__
                if getattr(value, "_node_spatial_index", None) is not None
                else None
            ),
        }
    if depth >= 4:
        return {"type": type(value).__name__, **({"count": len(value)} if isinstance(value, (tuple, list, dict)) else {})}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {"type": type(value).__name__, **{
            field.name: snapshot(getattr(value, field.name), depth + 1, _budget)
            for field in itertools.islice(dataclasses.fields(value), 30)
            if not field.name.startswith("_")
        }}
    if isinstance(value, dict):
        out = {str(k): snapshot(v, depth + 1, _budget) for k, v in itertools.islice(value.items(), 10)}
        if len(value) > 10:
            out["…"] = f"{len(value)} itens no total"
        return out
    if isinstance(value, (tuple, list)):
        out = [snapshot(v, depth + 1, _budget) for v in value[:8]]
        if len(value) > 8:
            out.append({"omitted": len(value) - 8, "total": len(value)})
        return out
    attrs = getattr(value, "__dict__", {})
    return {"type": type(value).__name__, **{
        k: snapshot(v, depth + 1, _budget) for k, v in itertools.islice(attrs.items(), 12)
        if not k.startswith("_") and isinstance(v, (str, int, float, bool, enum.Enum))
    }}


def layer_for(module, name):
    if module.startswith("router.core.data") and name in {
        "adaptive_zone", "graph_for_bbox", "build_query_graph", "query_bbox", "snap",
        "build_spatial_index", "query_node_ids", "query_arc_ids", "iter_out_arcs",
    }:
        return "01"
    if module.startswith("router.core.physics") and name in {
        "traverse", "traverse_arc", "equilibrium_speeds", "forces_at", "advance_speed",
        "gravity_force_n", "rolling_force_n", "aerodynamic_force_n",
        "equilibrium_speed_mps", "required_rider_power_w",
    }:
        return "02"
    if module.startswith("router.core.search") and name in {
        "search", "search_candidates", "bounds", "route_profile_for", "routes_from_result",
        "from_result", "order_labels", "select_distinct_routes", "arc_jaccard",
    }:
        return "03"
    if module.startswith(("router.core.prediction", "router.core.physiology")) and name in {"estimate_intensity", "segment_rpe", "segments_from_traversal", "predict", "load_predictor", "load_compatible", "extract_features", "predict_response", "estimate_rider_response"}:
        return "04"
    if module.startswith("router.core.evaluation") and name in {"assess_route", "assess_candidates", "classify_routes", "classify_route", "evaluate_candidates", "classify_safety"}:
        return "05"
    if module.startswith(("router.app", "router.contracts")) and name in {
        "plan_routes", "plan_routes_progressive", "plan_offline_routes",
        "to_contract", "_to_route_contract", "dumps", "_merge_leg_plans",
    }:
        return "06"
    return None


class LayerTrace:
    """Conta chamadas e amostra entradas, cálculos locais e retornos reais.

    Usa sys.monitoring quando disponível, com fallback para sys.settrace.
    Nenhuma função do motor é substituída.
    As durações são inclusivas e incluem o custo da instrumentação.
    """
    def __init__(self, samples=3):
        self.samples = samples
        self.counts = Counter()
        self.durations = Counter()
        self.events = []
        self._active = {}
        self._cache = {}
        self.sources = {}
        self.errors = []

    def __enter__(self):
        self.previous = sys.gettrace()
        self.previous_threads = threading.gettrace()
        if self.previous is not None or self.previous_threads is not None:
            raise RuntimeError("Desative o debugger Python para capturar as camadas, ou desligue a captura no formulário.")
        self.started = time.perf_counter()
        self._monitor = None
        monitoring = getattr(sys, "monitoring", None)
        if monitoring is not None:
            for candidate in (3, 4, 5):
                try:
                    monitoring.use_tool_id(candidate, "router-layer-trace")
                except ValueError:
                    continue
                self._monitor = (monitoring, candidate)
                break
        if self._monitor is None:
            threading.settrace(self._trace)
            sys.settrace(self._trace)
        else:
            monitor, tool = self._monitor
            events = monitor.events
            self._monitor_codes = []
            monitor.register_callback(tool, events.PY_START, self._monitor_start)
            monitor.register_callback(tool, events.PY_RETURN, self._monitor_return)
            monitor.register_callback(tool, events.PY_UNWIND, self._monitor_unwind)
            # Eventos locais somente nos pontos observados. Não instrumenta as
            # milhões de chamadas auxiliares feitas pela simulação física.
            codes = set()
            def add_function(value, module_name):
                if isinstance(value, (staticmethod, classmethod)):
                    value = value.__func__
                if inspect.isfunction(value) and value.__module__ == module_name:
                    if layer_for(module_name, value.__name__) is not None:
                        codes.add(value.__code__)
            for name, module in tuple(sys.modules.items()):
                if module is None or not name.startswith('router.'):
                    continue
                for value in tuple(vars(module).values()):
                    add_function(value, name)
                    if inspect.isclass(value) and getattr(value, '__module__', None) == name:
                        for method in tuple(vars(value).values()):
                            add_function(method, name)
            for code in codes:
                monitor.set_local_events(tool, code, events.PY_START | events.PY_RETURN | events.PY_UNWIND)
                self._monitor_codes.append(code)
        return self

    def __exit__(self, kind, exc, tb):
        if self._monitor is None:
            sys.settrace(self.previous)
            threading.settrace(self.previous_threads)
        else:
            monitor, tool = self._monitor
            for code in self._monitor_codes:
                monitor.set_local_events(tool, code, 0)
            for event in (monitor.events.PY_START, monitor.events.PY_RETURN, monitor.events.PY_UNWIND):
                monitor.register_callback(tool, event, None)
            monitor.free_tool_id(tool)
        self.elapsed = time.perf_counter() - self.started
        self.failure = None if exc is None else f"{kind.__name__}: {exc}"
        self._active.clear()
        return False

    def _monitor_start(self, code, offset):
        frame = sys._getframe(1)
        if self._trace(frame, "call", None) is None:
            return self._monitor[0].DISABLE
        return None

    def _monitor_return(self, code, offset, value):
        frame = sys._getframe(1)
        self._trace(frame, "return", value)
        layer, name = self._cache.get(code, (None, ""))
        if (layer is not None and self.counts[(layer, name)] >= self.samples
                and not any(item[2] == name for item in self._active.values())):
            return self._monitor[0].DISABLE
        return None

    def _monitor_unwind(self, code, offset, exc):
        frame = sys._getframe(1)
        self._trace(frame, "exception", (type(exc), exc, None))
        self._trace(frame, "return", None)
        return None

    def _trace(self, frame, event, arg):
        if event == "call":
            code = frame.f_code
            if code not in self._cache:
                module = frame.f_globals.get("__name__", "")
                self._cache[code] = (layer_for(module, code.co_name), f"{module}.{getattr(code, 'co_qualname', code.co_name)}")
            layer, function = self._cache[code]
            if layer is None:
                return None
            if self.counts[(layer, function)] >= self.samples:
                return None
            frame.f_trace_lines = False
            self.counts[(layer, function)] += 1
            sample = None
            if self.counts[(layer, function)] <= self.samples:
                sample = {"order": len(self.events) + 1, "layer": layer, "function": function,
                          "file": code.co_filename, "line": code.co_firstlineno,
                          "inputs": {k: snapshot(v) for k, v in itertools.islice(frame.f_locals.items(), 40)}, "exceptions": []}
                self.events.append(sample)
                if function not in self.sources:
                    try:
                        self.sources[function] = inspect.getsource(code)
                    except (OSError, TypeError):
                        self.sources[function] = "Fonte indisponível."
            self._active[id(frame)] = (time.perf_counter(), layer, function, sample)
            return self._trace
        active = self._active.get(id(frame))
        if active is None:
            return None
        started, layer, function, sample = active
        if event == "exception" and sample is not None:
            kind, exc, _ = arg
            sample["exceptions"].append(f"{kind.__name__}: {str(exc)[:500]}")
        if event == "return":
            elapsed = time.perf_counter() - started
            self.durations[(layer, function)] += elapsed
            if sample is not None:
                sample.update(seconds=elapsed, output=snapshot(arg), calculations={
                    k: snapshot(v) for k, v in itertools.islice(frame.f_locals.items(), 65)
                    if k not in sample["inputs"] and not k.startswith("_")
                })
            del self._active[id(frame)]
        return self._trace

    def report(self):
        return {"elapsed": getattr(self, "elapsed", 0), "failure": getattr(self, "failure", None),
                "events": self.events, "sources": self.sources, "samples_per_function": self.samples, "counts_are_samples": True,
                "functions": [{"layer": layer, "function": fn, "calls": count,
                               "inclusive_seconds": self.durations[(layer, fn)]}
                              for (layer, fn), count in self.counts.items()]}
