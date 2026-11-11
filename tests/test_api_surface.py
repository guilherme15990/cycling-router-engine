"""Contrato da superficie publica da API (fachada ``api`` + referencia gerada).

Garante que a fachada, a curadoria de camadas do gerador e o documento
``docs/reference/api.md`` permanecam **sincronizados** (ADR-029 D8 — regressoes).
Sem rede: apenas introspeccao do proprio codigo.

* ``api.__all__`` nao tem duplicatas e todo simbolo existe de fato;
* as camadas curadas em ``scripts/generate_api_reference.py`` cobrem
  ``api.__all__`` exatamente (nada a mais, nada a menos);
* o cabecalho de ``docs/reference/api.md`` declara a mesma versao e a mesma
  contagem de simbolos — pega uma referencia **nao regenerada**.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from router.interfaces import api
ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts" / "generate_api_reference.py"
REFERENCE = ROOT / "docs" / "reference" / "api.md"

#: Contagem congelada da fachada ampla (ADR-031, secao 7, Fase E).
FROZEN_API_SURFACE = 336


def _load_generator():
    spec = importlib.util.spec_from_file_location("_generate_api_reference", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_all_has_no_duplicates():
    exports = list(api.__all__)
    assert len(exports) == len(set(exports)), "api.__all__ tem duplicatas"


def test_wide_facade_is_frozen():
    assert len(api.__all__) == FROZEN_API_SURFACE, (
        "a fachada ampla e congelada (ADR-031); adicione simbolos novos em "
        "router.contracts ou router.integration"
    )


def test_every_exported_symbol_exists():
    missing = [name for name in api.__all__ if not hasattr(api, name)]
    assert missing == [], f"simbolos de __all__ ausentes na fachada: {missing}"


def test_generator_layers_match_exports_exactly():
    generator = _load_generator()
    declared = {name for _, _, names in generator.LAYERS for name in names}
    exported = set(api.__all__)
    assert declared == exported, (
        f"camadas desatualizadas; faltam={sorted(exported - declared)} "
        f"sobram={sorted(declared - exported)}"
    )


def test_reference_document_is_in_sync():
    text = REFERENCE.read_text(encoding="utf-8")
    expected = f"Versao: **{api.__version__}** - {len(api.__all__)} simbolos"
    assert expected in text, (
        "docs/reference/api.md desatualizado; rode "
        "`python scripts/generate_api_reference.py`"
    )
