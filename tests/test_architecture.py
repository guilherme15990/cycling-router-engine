"""Teste executavel das fronteiras do ADR-031 (secao 4.3).

Sem dependencia externa: usa ``ast`` para ler os imports de ``src/router`` e
comparar com a matriz permitida. Uma violacao aparece como erro de teste, nao
como convencao oral.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
ROUTER = SRC / "router"

ALLOWED: dict[str, set[str]] = {
    "contracts": set(),
    "offline": set(),
    "core": set(),
    "core.data": {"contracts"},
    "core.physics": {"core.data", "contracts"},
    "core.search": {"core.data", "core.physics", "contracts"},
    "core.prediction": {"core.data", "core.physics", "contracts"},
    "core.evaluation": {
        "core.data",
        "core.physics",
        "core.search",
        "core.prediction",
        "contracts",
    },
    "app": {
        "contracts",
        "offline",
        "core",
        "core.data",
        "core.physics",
        "core.search",
        "core.prediction",
        "core.evaluation",
    },
    "interfaces": {"app", "contracts", "offline", "top"},
    "top": {"interfaces"},
}

#: Prefixos de modulo -> camada. A ordem importa (mais especifico primeiro).
_LAYER_PREFIXES: tuple[tuple[str, str], ...] = (
    ("router.contracts", "contracts"),
    ("router.offline", "offline"),
    ("router.core.data", "core.data"),
    ("router.core.physics", "core.physics"),
    ("router.core.search", "core.search"),
    ("router.core.prediction", "core.prediction"),
    ("router.core.evaluation", "core.evaluation"),
    ("router.core", "core"),
    ("router.app", "app"),
    ("router.interfaces", "interfaces"),
    ("router", "top"),
)


def _layer(module: str) -> str | None:
    for prefix, layer in _LAYER_PREFIXES:
        if module == prefix or module.startswith(prefix + "."):
            return layer
    return None


def _file_layer(path: Path) -> str:
    parts = path.relative_to(SRC).with_suffix("").parts
    if path.name == "__init__.py":
        parts = parts[:-1]
    return _layer(".".join(parts)) or "top"


def _module_name(path: Path) -> str:
    parts = path.relative_to(SRC).with_suffix("").parts
    if path.name == "__init__.py":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve(path: Path, node: ast.ImportFrom) -> list[str]:
    if node.level == 0:
        return [node.module or ""]
    parts = _module_name(path).split(".")
    base = parts if path.name == "__init__.py" else parts[:-1]
    drop = node.level - 1
    if drop:
        base = base[:-drop]
    return [".".join(base + ([node.module] if node.module else []))]


def imports_in(path: Path) -> list[tuple[str, str]]:
    """Pares ``(camada_do_arquivo, camada_importada)`` de um arquivo."""
    source_layer = _file_layer(path)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    pairs: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        targets: list[str] = []
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            targets = _resolve(path, node)
        for module in targets:
            target = _layer(module)
            if target is not None:
                pairs.append((source_layer, target))
    return pairs


def test_import_boundaries():
    violations = [
        f"{source} -> {target} ({path.relative_to(ROOT)})"
        for path in sorted(ROUTER.rglob("*.py"))
        for source, target in imports_in(path)
        if target != source and target not in ALLOWED[source]
    ]
    assert violations == [], "violacoes de fronteira:\n" + "\n".join(violations)


def test_contracts_are_stdlib_only():
    for path in sorted((ROUTER / "contracts").rglob("*.py")):
        targets = {target for _, target in imports_in(path)} - {"contracts"}
        assert targets == set(), f"contracts importa dominio em {path.name}: {targets}"


def test_interfaces_do_not_touch_core():
    for path in sorted((ROUTER / "interfaces").rglob("*.py")):
        targets = {target for _, target in imports_in(path)}
        assert not any(target.startswith("core") for target in targets), (
            f"interfaces importa nucleo em {path.name}: {targets}"
        )


def test_root_package_only_exposes_version():
    import router

    assert router.__all__ == ["__version__"]
    assert not hasattr(router, "plan_routes")


def test_no_top_level_domain_imports():
    legacy = [
        "graph",
        "osm",
        "physical",
        "physiology",
        "search",
        "ml",
        "intelligence",
        "evaluation",
        "planning",
        "multi_leg",
        "integration",
        "api",
        "cli",
        "geo",
        "envelope",
        "snapping",
        "elevation",
        "elevation_tiles",
        "route_contract",
        "offline",
    ]
    assert not any((SRC / name).exists() for name in legacy), "shim/top-level legado restante"

    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if ROUTER in path.parents:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = [(node.module or "").split(".")[0]]
            for module in modules:
                if module in legacy:
                    offenders.append(f"{path.relative_to(ROOT)}: {module}")
    assert offenders == [], "imports curtos legados:\n" + "\n".join(offenders)
