# 🚴 Cycling Routing Engine (Offline & Multi-Objective)

> Motor de roteamento cicloviário de alta fidelidade, **100% offline** e desenvolvido em **Python puro**, combinando simulação dinâmica longitudinal real, busca de fronteira Pareto (MOA\*), validação estrita de circulação e contratos fisiológicos auditáveis.

O projeto separa quatro decisões: a física calcula as métricas; a busca encontra alternativas não dominadas; o ML pode gerar uma candidata independente; e a política do perfil ordena e limita o que será apresentado. A candidata ML passa pelas mesmas validações físicas e de circulação. Se coincidir com uma rota Pareto, o resultado preserva as duas origens.

---

### ✨ O que torna este motor diferente?

A maioria dos roteadores convencionais trata o relevo e o esforço como penalidades estáticas de peso em uma aresta. Este motor modela a travessia como um **problema físico e dinâmico real**:

- **Física Longitudinal Real:** Modela aceleração, desaceleração, atrito de rolamento ($C_{rr}$), arrasto aerodinâmico quadrático ($C_d \cdot A$), gradiente altimétrico, vento e dissipação de energia por frenagem. A velocidade de saída de um trecho é propagada como velocidade de entrada do próximo trecho.
- **Busca Multiobjetivo (A\* Pareto):** Em vez de reduzir múltiplos objetivos a um número arbitrário, o motor explora a fronteira de Pareto (tempo, distância, ganho altimétrico e energia mecânica) com dominância discretizada por velocidade (`SpeedBucketDominance`).
- **Autonomia Offline Total:** Não depende de serviços externos ou bibliotecas em C/Rust para o núcleo:
  - Parser nativo de arquivos `OSM.PBF` (varints, zigzag, protobuf em duas passagens).
  - Decodificador nativo de tiles de elevação `Terrarium PNG` (com implementação dos filtros de linha PNG).
  - Guardrail de rede que impede conexões não autorizadas em tempo de execução.
- **Guardrails Operacionais e de Circulação:** Aplicação estrita de restrições de conversão OSM (`via_node` e `via_way`), regras de acesso condicionais e tratamento formal de trechos a pé (`bicycle=dismount`).
- **Honestidade Científica em ML:** Se não houver um modelo calibrado com dados reais, o motor declara explicitamente a ausência (`UNAVAILABLE` / `MODEL_NOT_CALIBRATED`) e opera em modo puramente físico (`PHYSICAL_ONLY`), recusando-se a inventar valores de percepção de esforço (Borg) ou frequência cardíaca.

---

### 🎯 Perfis de Rota Disponíveis

| Perfil | Objetivos na Fronteira | Estratégia de Ranking |
| :--- | :--- | :--- |
| **Mais Rápido** (`fastest`) | Tempo + Ganho Altimétrico | Lexicográfico com tolerância |
| **Mais Curto** (`shortest`) | Distância + Tempo | Lexicográfico com tolerância |
| **Mais Plano** (`flattest`) | Ganho Altimétrico + Distância | Lexicográfico com tolerância |
| **Equilibrado** (`balanced`) | Tempo + Distância + Ganho Altimétrico | Score ponderado normalizado |
| **Elétrico** (`electric`) | Energia Mecânica + Tempo | Lexicográfico com tolerância + Viabilidade de bateria (Wh) |

O perfil escolhe a política de ordenação; ele não altera a física da travessia. O perfil elétrico usa trabalho mecânico e não inventa autonomia ou descarga de bateria quando esses dados não foram fornecidos. Veja [busca física, precisão e benchmarks](docs/physical-search.md).

### 🔋 Energia cinética e teto de velocidade

O planejamento da API não aplica um teto global de velocidade por padrão. A
velocidade evolui pela equação de energia, arrasto, rolamento e relevo; um teto
só entra quando o chamador fornece `Environment(safe_max_speed_mps=...)` por
uma exigência operacional. O resultado informa separadamente:

- `delta_kinetic_j`: variação da energia cinética (`½·m·v²`);
- `dissipative_loss_j`: energia realmente dissipada por arrasto, rolamento ou
  frenagem;
- `kinetic_assist_j`: energia cinética aproveitada durante trechos de subida;
- `climb_entry_kinetic_j`: energia cinética disponível na entrada das subidas;
- `energy_loss_detected`: resposta direta à pergunta “a rota perdeu energia?”;
- `braking_energy_j`: parcela dissipada especificamente por frenagem.

Assim, uma queda de energia cinética numa subida não é confundida com perda
irreversível: ela aparece como conversão para energia potencial, enquanto a
perda real fica explícita nos campos de dissipação.

Em uma subida, `kinetic_assist_j` é acumulada continuamente como a parcela de
`E_k` consumida contra o ganho de altura. Isso permite comparar uma rota que
desce para ganhar embalo antes de uma subida longa com uma rota que sobe
diretamente, sem transformar a preferência em um simples limite de velocidade.

---

### ⚙️ Requisitos e Instalação

Requer Python 3.10 ou mais recente. O núcleo não possui dependências obrigatórias.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Para recursos opcionais de DEM:

```powershell
python -m pip install -e ".[dem]"
```

Também é possível executar diretamente a partir da raiz:

```powershell
$env:PYTHONPATH = "src"
```

---

### 🚀 Uso Mínimo

A superfície recomendada para integrações é `router.integration`.

```python
from router.integration import MobilityEngine

engine = MobilityEngine.from_pbf(
    "data/sources/regiao.osm.pbf",
    elevation_tiles="data/dem",
)

plan = engine.plan(
    origin=(-17.804347455072403, -50.92253569751691),
    destination=(-17.770388956200584, -50.920569302992085),
    vehicle="bicycle",
    propulsion_type="manual",
    profile="flattest",
)

print(plan.to_json(indent=2))
```

O grafo e a elevação são carregados localmente — nenhuma etapa de planejamento baixa dados da internet. Para reutilizar uma rede preparada:

```python
from router.integration import MobilityEngine, load_graph, save_graph

prepared = engine.prepare(vehicle="bicycle", propulsion_type="manual")
save_graph(prepared, "cache/bicycle.json")
engine = MobilityEngine(load_graph("cache/bicycle.json"))
```

---

### 🏋️ Presets Manuais e RPE

Para bicicletas manuais, o RPE estimado usa a potência humana produzida pela simulação e a potência de referência do perfil. Peso corporal e idade podem ser informados.

| Perfil | Potência de referência |
| :--- | ---: |
| Sedentário | 75 W |
| Iniciante | 112,5 W |
| Recreativo | 150 W |
| Treinado | 225 W |
| Competitivo | 315 W |
| Elite | 412,5 W |

> São presets provisórios, versionados como `manual-rider-presets@1`. Não são FTP ou medições individuais e não representam potência aplicada continuamente.

```python
from router.integration import ManualRiderProfile

rider = ManualRiderProfile(
    profile="recreational",
    mass_kg=75,
    age_years=35,
)

plan = engine.plan(origin, destination, rider=rider)
for route in plan.routes:
    effort = route.effort_estimate
    print(effort["mean_rpe"], effort["peak_rpe"])
```

O RPE segue a escala Borg 6–20 e é uma estimativa computacional não calibrada (`calibrated=False`, `is_measurement=False`).

---

### 🧠 Rota por ML (Candidata Neural)

O ML tem uma função específica: gerar uma rota candidata independente — ele não apenas classifica as rotas retornadas pela busca Pareto.

```python
plan = engine.plan(
    origin,
    destination,
    profile="flattest",
    rider=rider,
    ml_route=True,
)

for route in plan.routes:
    print(route.origins, route.recommended, route.generation_model_version)

model_dict = plan.diagnostics["neural_routing"]["model"]
```

A rede atual tem sete entradas, oito neurônios ocultos, uma saída por objetivo, 180 épocas e seed determinística 42. Os alvos vêm da simulação física local (`simulation_surrogate`, `calibrated=False`). Veja [candidata neural e limites de calibração](docs/ml-candidate-design.md).

---

### 🔀 Quantidade e Semelhança das Rotas

`max_routes` limita apenas a apresentação; a busca ainda pode encontrar a fronteira completa.

- `max_routes=None` ou `0` mantém todas as candidatas Pareto.
- Um limite positivo mantém até N candidatas Pareto e preserva a candidata ML, podendo resultar em N+1 rotas.
- `collapse_similar=True` remove duplicatas exatas e agrupa rotas semelhantes por Jaccard dos arcos direcionados.
- Sem limite explícito, o agrupamento usa `structural-natural-break@1`: o corte vem do maior salto na árvore de similaridade, sem percentual fixo.

```python
plan = engine.plan(
    origin,
    destination,
    profile="balanced",
    max_routes=3,
    collapse_similar=True,
    ml_route=True,
)
```

`max_expansions` e `max_labels_per_state` são limites internos da exploração. Quando atingidos, o diagnóstico marca a busca como truncada — isso não é apresentado como ótimo global.

---

### 📍 Múltiplas Paradas e Contrato

`plan_routes` resolve as pernas na ordem das paradas, usa o mesmo grafo e funde geometria e métricas.

```python
from router.integration import plan_routes

plan = plan_routes(
    graph,
    origin,
    destination,
    stops=[stop_a, stop_b],
    cyclist=cyclist,
    bicycle=bicycle,
    profile="fastest",
)
```

Os contratos públicos estão em `router.contracts`. O envelope é versionado, rejeita versões desconhecidas e preserva geometria, fontes de parâmetros, incerteza de elevação, RPE e métricas físicas. `None` significa não calculado; não é convertido silenciosamente em zero.

```python
from router.integration import dumps, loads

payload = loads(dumps(plan.to_contract(), indent=2))
```

---

### 🖥️ CLI

```powershell
python scripts/mobility_route.py `
  --pbf data/sources/regiao.osm.pbf `
  --dem-dir data/dem `
  --origin=-17.804347455072403,-50.92253569751691 `
  --destination=-17.770388956200584,-50.920569302992085 `
  --vehicle bicycle `
  --propulsion manual `
  --output debug/.cache/mobility-plan.json
```

---

### 🧪 Testes

```powershell
pytest
```

Os testes cobrem física, busca, Pareto, circulação, OSM PBF, DEM, energia, presets manuais, intensidade/RPE, rede neural, contratos, múltiplas paradas e guardrails offline.

---

### 📚 Toolkit de Mobilidade e Documentação

A engine também oferece uma API orientada à estrada, com presets leves por veículo/propulsão e extensões para tração e resistência, preservando a API ciclável.

| Documento | Conteúdo |
| :--- | :--- |
| [`docs/mobility-toolkit.md`](docs/mobility-toolkit.md) | Mobilidade, energia, categorias e propulsão |
| [`docs/physical-search.md`](docs/physical-search.md) | Busca física e aproximações numéricas |
| [`docs/ml-candidate-design.md`](docs/ml-candidate-design.md) | Candidata neural e limites de calibração |
| [`docs/reference/quickstart.md`](docs/reference/quickstart.md) | Receitas da API e do contrato |
| [`docs/reference/api.md`](docs/reference/api.md) | Referência pública gerada |

---

### 📄 Licença

MIT. Consulte `pyproject.toml` para os metadados do pacote.
