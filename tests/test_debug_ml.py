"""Pesos observados no debug, sem alterar a inferência do motor."""
import json
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "debug"))
from layer_trace import LayerTrace
from ml_debug import explain_model
from router.core.prediction.models import ConstantModel, LinearModel, ModelError


def test_trace_preserves_all_features_and_explains_signed_contributions():
    names = tuple(f"feature_{index}" for index in range(12))
    model = LinearModel(names, (2., -1., *([0.] * 10)), 6.)
    row = dict.fromkeys(names, 0.)
    row.update(feature_0=3., feature_1=2., unused=100.)
    with LayerTrace(samples=1) as trace:
        prediction = model.predict_row(row)
    report = json.loads(json.dumps(trace.report()))
    event = next(event for event in report["events"] if event.get("ml"))
    explanation = event["ml"]
    rows = explanation["rows"]
    assert len(rows) == 13  # Generic snapshots truncate lists after eight items.
    assert rows[0]["coefficient_percent"] == pytest.approx(200 / 3)
    assert rows[0]["contribution_percent"] == 75
    assert rows[1]["contribution"] == -2
    assert rows[1]["contribution_percent"] == 25
    assert rows[2]["contribution_percent"] == 0
    assert not rows[-1]["used"]
    assert rows[-1]["coefficient_percent"] is None
    assert sum(item["contribution_percent"] or 0 for item in rows) == pytest.approx(100)
    assert explanation["intercept"] + sum(item["contribution"] or 0 for item in rows) == prediction == event["output"]


def test_missing_feature_does_not_create_a_percentage_or_prediction():
    model = LinearModel(("known", "missing"), (1., -1.), 6.)
    with LayerTrace(samples=1) as trace:
        with pytest.raises(ModelError):
            model.predict_row({"known": 2., "missing": None})
    explanation = next(event["ml"] for event in trace.report()["events"] if event.get("ml"))
    assert not explanation["complete"]
    assert all(item["contribution_percent"] is None for item in explanation["rows"])
    assert explanation["rows"][1]["value"] is None


@pytest.mark.parametrize("coefficient,value", [(0., 10.), (1., 0.)])
def test_zero_denominators_remain_unavailable(coefficient, value):
    explanation = explain_model(LinearModel(("x",), (coefficient,), 6.), {"x": value})
    assert explanation["rows"][0]["contribution"] == 0
    assert explanation["rows"][0]["contribution_percent"] is None


def render_panel():
    import streamlit as st
    from layer_lab import _table
    from ml_debug import render
    render(st.session_state.get("report"), _table)


def test_panel_shows_unavailable_state_and_full_contract():
    from router.core.prediction.features import ALL_FEATURE_NAMES
    app = AppTest.from_function(render_panel).run()
    assert not app.exception
    assert "não executa ML" in app.info[0].value
    text = "\n".join(item.value for item in app.markdown)
    assert all(name in text for name in ALL_FEATURE_NAMES)
    assert "0.00%" not in text


@pytest.mark.parametrize("model", [LinearModel(("x",), (-2.,), 6.), ConstantModel(13.)])
def test_panel_renders_captured_linear_and_constant_models(model):
    with LayerTrace(samples=1) as trace:
        model.predict_row({"x": 2.})
    app = AppTest.from_function(render_panel)
    app.session_state["report"] = trace.report()
    app.run()
    assert not app.exception
    if isinstance(model, LinearModel):
        text = "\n".join(item.value for item in app.markdown)
        assert "100.00%" in text
        assert "| -4 |" in text
    else:
        assert "Modelo constante" in app.info[0].value
