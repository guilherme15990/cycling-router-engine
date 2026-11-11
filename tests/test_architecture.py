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
    "core.physiology": set(),
    "core.search": {"core.data", "core.physics", "contracts"},
    "core.prediction": {
        "core.data",
        "core.physics",
        "core.physiology",
        "contracts",
    },
    "core.evaluation": {
        "core.data",
        "core.physics",
        "core.physiology",
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
        "core.physiology",
        "core.search",
        "core.prediction",
        "core.evaluation",
    },
    "interfaces": {"app", "contracts", "offline", "integration", "top"},
    "integration": {"app", "contracts", "offline"},
    "top": {"interfaces", "integration"},
}

#: Prefixos de modulo -> camada. A ordem importa (mais especifico primeiro).
_LAYER_PREFIXES: tuple[tuple[str, str], ...] = (
    ("router.contracts", "contracts"),
    ("router.offline", "offline"),
    ("router.core.data", "core.data"),
    ("router.core.physics", "core.physics"),
    ("router.core.physiology", "core.physiology"),
    ("router.core.search", "core.search"),
    ("router.core.prediction", "core.prediction"),
    ("router.core.evaluation", "core.evaluation"),
    ("router.core", "core"),
    ("router.app", "app"),
    ("router.interfaces", "interfaces"),
    ("router.integration", "integration"),
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


# ---------------------------------------------------------------------------
# E4 (ADR-033 secao 9): invariantes verificaveis
# ---------------------------------------------------------------------------

#: Fachadas de estagio de ``core`` (pacotes que agregam um estagio do pipeline).
_SIBLING_STAGE_FACADES = frozenset(
    {
        "router.core.data",
        "router.core.physics",
        "router.core.search",
        "router.core.prediction",
        "router.core.evaluation",
    }
)


def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def _iter_import_nodes(tree: ast.AST):
    """Percorre imports de runtime.

    Imports sob ``if TYPE_CHECKING:`` sao **type-only** e nao existem em tempo
    de importacao: sao ignorados na deteccao de ciclos de **import**. Imports
    tardios (dentro de funcoes) contam, porque sao arestas reais do grafo.
    """

    def walk(node: ast.AST):
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            for child in node.orelse:
                yield from walk(child)
            return
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node
        for child in ast.iter_child_nodes(node):
            yield from walk(child)

    yield from walk(tree)


def _module_names() -> set[str]:
    return {_module_name(path) for path in ROUTER.rglob("*.py")}


def _module_edges() -> dict[str, set[str]]:
    """Grafo modulo -> modulos ``router`` importados (nos existentes)."""
    names = _module_names()
    edges: dict[str, set[str]] = {name: set() for name in names}
    for path in sorted(ROUTER.rglob("*.py")):
        source = _module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in _iter_import_nodes(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                targets = _resolve(path, node)
            for target in targets:
                if target in names and target != source:
                    edges[source].add(target)
    return edges


def _strongly_connected(edges: dict[str, set[str]]) -> list[list[str]]:
    """Algoritmo de Tarjan: componentes fortemente conectados (>1 = ciclo)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    stack: list[str] = []
    result: list[list[str]] = []
    counter = [0]

    def strongconnect(node: str) -> None:
        index[node] = low[node] = counter[0]
        counter[0] += 1
        stack.append(node)
        on_stack[node] = True
        for neighbour in edges.get(node, ()):  # noqa: B007
            if neighbour not in index:
                strongconnect(neighbour)
                low[node] = min(low[node], low[neighbour])
            elif on_stack.get(neighbour):
                low[node] = min(low[node], index[neighbour])
        if low[node] == index[node]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack[member] = False
                component.append(member)
                if member == node:
                    break
            result.append(component)

    for node in sorted(edges):
        if node not in index:
            strongconnect(node)
    return result


def test_no_import_cycles():
    """O grafo de imports de runtime de ``router`` e um DAG.

    Imports apenas de tipo (``if TYPE_CHECKING:``) nao contam: nao existem em
    tempo de importacao. Imports tardios (dentro de funcoes) contam.
    """
    cyclic = [
        sorted(component)
        for component in _strongly_connected(_module_edges())
        if len(component) > 1
    ]
    assert cyclic == [], "ciclos de import:\n" + "\n".join(str(c) for c in cyclic)


def test_core_does_not_import_sibling_stage_facade():
    """``core`` importa o modulo definidor, nunca a fachada de estagio irmao.

    Excecoes admitidas (ADR-033, 4.1.8): ``core.physiology`` e
    ``core.data.elevation`` (contrato/infraestrutura), que **nao** estao no
    conjunto de fachadas de estagio.
    """
    offenders: list[str] = []
    for path in sorted((ROUTER / "core").rglob("*.py")):
        own = "router." + _file_layer(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                target = node.module or ""
                if target in _SIBLING_STAGE_FACADES and target != own:
                    offenders.append(f"{path.relative_to(ROOT)}: {target}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in _SIBLING_STAGE_FACADES and alias.name != own:
                        offenders.append(f"{path.relative_to(ROOT)}: {alias.name}")
    assert offenders == [], "core importa fachada de estagio irmao:\n" + "\n".join(
        offenders
    )


def test_app_legacy_has_no_definitions():
    """``app.legacy`` e artefato de compatibilidade: apenas reexportacoes."""
    tree = ast.parse((ROUTER / "app" / "legacy.py").read_text(encoding="utf-8"))
    definitions = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    assert definitions == [], (
        f"app.legacy deve conter apenas reexportacoes; definicoes: {definitions}"
    )


def test_pyproject_version_matches_package():
    """Versao unica: ``pyproject.toml`` e ``router.__version__`` concordam."""
    import tomllib

    import router

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["version"] == router.__version__


def test_integration_top_level_imports_are_leaf_only():
    """``router.integration`` carrega no topo apenas ``contracts``/``offline``.

    Os nomes de ``router.app`` (que arrastam o nucleo) sao resolvidos por
    ``__getattr__`` e **nao** podem voltar ao topo (ADR-033, E5).
    """
    tree = ast.parse((ROUTER / "integration.py").read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in tree.body:
        targets: list[str] = []
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                offenders.append(f"import relativo (level={node.level})")
                continue
            targets = [node.module or ""]
        else:
            continue
        for target in targets:
            layer = _layer(target)
            if layer not in (None, "contracts", "offline"):
                offenders.append(f"{target} -> camada {layer!r}")
    assert offenders == [], "router.integration importa o nucleo no topo:\n" + "\n".join(
        offenders
    )


def test_planning_does_not_project_contract():
    """A projecao ``RoutePlanResult -> RoutePlan`` vive em ``app/plan_view.py``."""
    source = (ROUTER / "app" / "planning.py").read_text(encoding="utf-8")
    assert "def _to_bbox_contract" not in source
    assert "def _to_route_contract" not in source
    assert "RoutePlan(" not in source, "planning ainda constroi o contrato"
    assert (ROUTER / "app" / "plan_view.py").exists()


# ---------------------------------------------------------------------------
# E6 (ADR-033 secao 9): a CLI e o tooling sao decompostos por responsabilidade
# ---------------------------------------------------------------------------

CLI = ROUTER / "interfaces" / "cli"


def _top_level_definitions(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]


def test_cli_init_is_only_orchestration():
    """``cli/__init__.py`` define apenas ``main`` (ADR-033, E6).

    O parser, a renderizacao e os comandos moram em submodulos dedicados; a
    fachada da CLI nao os concentra.
    """
    assert _top_level_definitions(CLI / "__init__.py") == ["main"]
    assert (CLI / "parser.py").is_file()
    assert (CLI / "render.py").is_file()
    assert (CLI / "commands" / "__init__.py").is_file()


def test_cli_submodules_own_their_responsibility():
    """Cada submodulo da CLI expoe so a sua responsabilidade (ADR-033, E6)."""
    import router.interfaces.cli.commands as commands
    import router.interfaces.cli.parser as parser
    import router.interfaces.cli.render as render

    assert parser.__all__ == ["build_parser"]
    assert _top_level_definitions(CLI / "parser.py") == ["build_parser"]

    assert "configure_stdout" in render.__all__
    assert all(
        name.startswith("print_") or name == "configure_stdout"
        for name in render.__all__
    )
    render_defs = _top_level_definitions(CLI / "render.py")
    assert render_defs, "render.py sem funcoes"
    assert set(render_defs) == set(render.__all__)

    assert set(commands.__all__) == {
        "cmd_build",
        "cmd_fetch_dem",
        "cmd_geojson",
        "cmd_physical",
        "cmd_plan",
        "cmd_snap",
        "cmd_stats",
        "cmd_validate",
    }


def test_tooling_is_a_package_split_by_use_case():
    """``app/tooling`` e um pacote agregador, sem definicoes proprias (E6)."""
    tooling_dir = ROUTER / "app" / "tooling"
    assert tooling_dir.is_dir()
    for module in (
        "ingest",
        "graph",
        "elevation",
        "simulation",
        "prediction",
        "planning",
    ):
        assert (tooling_dir / f"{module}.py").is_file(), f"tooling/{module}.py ausente"

    assert _top_level_definitions(tooling_dir / "__init__.py") == []


def test_tooling_reexports_are_unique_and_importable():
    """``app.tooling.__all__`` e unico e cada nome existe no pacote."""
    import router.app.tooling as tooling

    assert len(tooling.__all__) == len(set(tooling.__all__)), "__all__ com duplicatas"
    for name in tooling.__all__:
        assert hasattr(tooling, name), f"tooling.{name} nao reexportado"
