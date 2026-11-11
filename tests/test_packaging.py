"""Empacotamento e identidade do pacote (ADR-032 secao 6; ADR-033, E7).

Verificacoes estruturais que **nao** exigem um build de wheel: versao unica
(``router.__version__ == pyproject.version``), descoberta de pacotes restrita
a ``router*``, marcadores PEP 561 (``py.typed``) por pacote, ausencia do
``py-modules`` legado e console scripts apontando para a CLI.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
ROUTER = SRC / "router"
PYPROJECT = ROOT / "pyproject.toml"


def _config() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_version_is_single_source():
    """A versao do ``pyproject`` e a do pacote concordam (ADR-032, 6.2)."""
    import router

    assert _config()["project"]["version"] == router.__version__


def test_package_discovery_is_scoped_to_router():
    """So ``router*`` e descoberto a partir de ``src`` (ADR-032, 6.3)."""
    find = _config()["tool"]["setuptools"]["packages"]["find"]
    assert find["where"] == ["src"]
    assert find["include"] == ["router*"]


def test_no_legacy_py_modules():
    """``py-modules = ["__main__"]`` e ``src/__main__.py`` ficaram para tras."""
    assert "py-modules" not in _config()["tool"]["setuptools"]
    assert not (SRC / "__main__.py").exists()
    assert (ROUTER / "__main__.py").is_file(), "python -m router depende disso"


def test_py_typed_marker_is_distributed():
    """``py.typed`` acompanha o pacote via ``package-data`` (PEP 561)."""
    data = _config()["tool"]["setuptools"]["package-data"]
    assert "py.typed" in data["*"]
    assert (ROUTER / "py.typed").is_file()


def test_every_package_ships_py_typed():
    """Cada pacote ``router.*`` traz o marcador de tipos inline."""
    missing = [
        init.parent.relative_to(ROOT).as_posix()
        for init in sorted(ROUTER.rglob("__init__.py"))
        if not (init.parent / "py.typed").is_file()
    ]
    assert missing == [], "pacotes sem py.typed:\n" + "\n".join(missing)


def test_console_scripts_point_to_cli_main():
    """Os dois scripts publicos entram na CLI (ADR-032, 6.1)."""
    scripts = _config()["project"]["scripts"]
    assert scripts == {
        "roteamento": "router.interfaces.cli:main",
        "router": "router.interfaces.cli:main",
    }
