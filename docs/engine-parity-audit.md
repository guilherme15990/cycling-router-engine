# Auditoria Python / TopoRide — 17/11/2026

Referência: Python `cycling-router-engine` e TypeScript em `toporide + nova engine`.
Auditoria em andamento: os testes abaixo comprovam paridade nos cenários indicados;
não certificam um porte integral de todas as APIs do Python.

## Evidência executada

| Camada | Resultado | Limite da evidência |
| --- | --- | --- |
| Forças e dinâmica | 264 cenários sem divergências acima de 1e-8 absoluto + 1e-10 relativo | 5 inclinações, velocidades 0/5/15 m/s, potência constante e ritmo automático, vento escalar, teto opcional; mais 24 casos de vento vetorial |
| Busca física | 30 casos com os mesmos caminhos e métricas | 5 perfis × 3 ventos × velocidade exata/agrupada; grafo pequeno preparado |
| Treino ML | 5 perfis sem divergências acima de 1e-9 nos parâmetros e influência | Mesmas amostras físicas ordenadas; comparação de pesos, vieses, estatísticas e MSE |
| Rio Verde com DEM real salvo | Mesmas 51 sequências de arcos Pareto nas duas engines; sem truncamento | Grafo hidratado e snapping do TopoRide compartilhados; perfil flattest, vento zero, 150 W |
| Melhor rota real | 1304,783951 s; 82,619338 m de subida; 127732,953727 J | Distância TS 5556,677772 m / Python 5556,679 m; diferença de 1,23 mm pelo arredondamento de distância no Python |

## Correções aplicadas

- Python: `Environment.wind_speed_mps` e `wind_from_deg`, projetados pelo azimute
  médio do arco. Entrada escalar `headwind_mps` preservada. Vetor informado tem
  precedência; geometria ausente com vento vetorial não nulo gera erro explícito.
- TopoRide: IDs de vias OSM, tags e limite de velocidade preservados na extração,
  pacote de cidade e montagem do grafo. `maxspeed` reconhece km/h e mph.
- TopoRide: extração PBF usa `getRefIds`, preservando os IDs originais dos nós.
- TopoRide: montagem descarta arcos que retornam ao mesmo nó, como o contrato Python.
- TopoRide: inicialização ML usa o mesmo MT19937 do Python. Mesma regra de
  amostragem, no grafo após snapping. Identificadores JSON dos modelos permanecem diferentes.

Pacotes locais antigos não contêm os metadados que já foram descartados.
Reextrair o pacote é necessário para recuperar IDs, tags e limites dessas vias.
Tags preservadas não significam que todas as regras condicionais já são aplicadas.

## Divergências ainda abertas e solução escolhida

| Camada | Diferença confirmada por leitura de código | Direção da correção |
| --- | --- | --- |
| Aquisição OSM | Filtros de classes e acesso diferentes; TS perde relações de conversão | Portar hierarquia de acesso Python e relações OSM completas; preservar tags e IDs |
| Grafo | TS agrega coordenadas sem IDs como fallback; mínimo de aresta 1 m contra 0,5 m | Preferir identidade OSM; testar explicitamente dados sem IDs e pontes/túneis |
| DEM | TS aplica mediana, suavização de nós e ancoragem; Python usa média móvel preservando extremos | Comparar com perfis conhecidos antes de escolher; não declarar um filtro superior apenas por produzir menos subida |
| Ausência de relevo | Hidratação TS pode preencher ausência com zero e apresentar cobertura conhecida | Preservar ausência/qualidade explícita conforme contrato Python |
| Snapping | Algoritmos diferentes; o teste real compartilhou o snapping TS | Portar candidatos/seleção conectada Python e verificar geometria e restrições após divisão |
| Busca | TS mantém arco de chegada em toda chave; Python só exige na dominância física com restrição. ML Python arredonda bucket; TS usa floor | Uniformizar política de estado e bucket com testes de fronteira; manter contexto legal necessário |
| Limites | UI TS inicia com 60000 expansões; Python sem limite por padrão | Tornar configuração efetiva explícita na comparação; continuar sinalizando truncamento |
| ML | JSON/hash específicos; viabilidade e estados da busca aprendida têm diferenças | Contrato intercambiável versionado e testes de busca com o mesmo modelo, inclusive stall e restrições |
| Fluidez | Opção `conserveMomentum` TS acrescenta objetivo; Python mede assistência cinética, mas não expõe a mesma opção de busca | Definir objetivo comum com testes de descida antes de subida; telemetria sozinha não certifica preferência |
| Múltiplas pernas | TS combina por algoId; Python tem composição e validação próprias | Comparar combinações completas e preservar diagnóstico de cada perna |
| Segurança e contrato | TS não possui toda a camada Python de rejeição/qualidade/incerteza | Portar o contrato e manter rotas rejeitadas auditáveis; não marcar rota inviável como recomendada |
| Mobilidade | TS nativo ainda focado em bicicleta manual; Python possui outras propulsões e categorias | Portar modelos e contratos por veículo; perfil de objetivo electric não equivale a propulsão elétrica |

## Reprodução

Na raiz Python:

```powershell
python scripts/audit_physics_parity.py .cache/parity-audit/physics-python.json
& 'C:\Users\sus\Documents\$apps\toporide + nova engine\node_modules\.bin\tsx.cmd' scripts/audit_toporide_physics.mts 'C:\Users\sus\Documents\$apps\toporide + nova engine' .cache/parity-audit/physics-python.json .cache/parity-audit/physics-after.json
```

O runner retorna código 1 se física, caminhos ou treino ML divergirem acima das
tolerâncias declaradas. O oracle é regenerado pela implementação Python atual.

O caso real reutiliza o arquivo OSM/DEM local `.cache/toporide-bbox-auto/real-data.json`:

```powershell
& 'C:\Users\sus\Documents\$apps\toporide + nova engine\node_modules\.bin\tsx.cmd' scripts/audit_saved_route.mts 'C:\Users\sus\Documents\$apps\toporide + nova engine' .cache/toporide-bbox-auto/real-data.json .cache/parity-audit/saved-graph.json
python scripts/audit_saved_route.py .cache/parity-audit/saved-graph.json .cache/parity-audit/saved-comparison.json
```

Esse arquivo real anterior não contém IDs/tags recuperados pela correção de
ingestão. Ele serve para isolar física e busca, não para certificar ingestão nova.

## Validação desta rodada

- TopoRide: 466 testes aprovados; TypeScript e build aprovados.
- Python: 77 testes focados de vento, dinâmica, API de mobilidade e física aprovados.
- Suíte Python ampla não concluída nesta rodada: houve execuções sem progresso
  observável, interrompidas. Não se atribui a causa às mudanças sem diagnóstico.
- Testes comparativos adicionais: 264 físicos, 30 de busca, 5 de treino ML e
  51 caminhos reais comparados; nenhuma divergência acima das tolerâncias indicadas.
