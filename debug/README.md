# Debug da MobilityEngine

O app principal chama diretamente `router.integration.MobilityEngine.plan`.
O worker transporta `MobilityPlan` no formato `roteamento.mobility-route-plan`,
sem converter métricas para o antigo contrato de bicicletas.

```powershell
python -m streamlit run debug/app.py
```

## Consulta

1. Carregue um grafo v4 local ou prepare uma rede pelo PBF no painel lateral.
2. Escolha origem e destino no mapa ou informe as coordenadas.
3. Selecione veículo, propulsão e perfil de rota. `flattest` é o perfil inicial.
4. Em bicicleta manual, selecione o perfil do ciclista e confira os campos
   preenchidos pela API. Informe peso e, opcionalmente, idade. A referência
   fixa aparece em W. A API ajusta automaticamente a potência à demanda de cada
   trecho, limitada pela capacidade do perfil, buscando um ritmo provisório de
   18 km/h. Não há campo de potência manual para bicicletas com perfil.
   Peso inicial de 75 kg é provisório. Nos demais veículos, peso, carga e potência
   ficam nos refinamentos opcionais; zero mantém o preset. Potência e unidade
   desses veículos são preservadas ao alternar para bicicleta manual e voltar.
5. Clique em **Executar API e capturar camadas**.

A consulta percorre toda a rede preparada, inclusive desvios fora do corredor
entre os pontos. A bbox serve à visualização; não recorta a busca.
A tela principal exercita a consulta origem–destino da toolkit. Em bicicletas
manuais, solicita também o baseline de RPE por perfil. Ritmo adaptativo e
composição de paradas da API anterior não são executados por ela.

## Rede por categoria e DEM

Uma rede preparada para bicicleta não é usada como se contivesse todas as vias
para automóveis. Ao trocar de classe, o worker reconstrói a rede a partir do
PBF informado e aplica os tiles Terrarium locais de `data/dem`. Sem esse PBF,
uma categoria incompatível produz erro explícito.

As redes preparadas são persistidas em `debug/.cache/mobility/`, por categoria,
fonte PBF e assinatura dos tiles DEM. Consultas seguintes reutilizam a preparação.
O construtor do painel lateral também usa `MobilityEngine.from_pbf().prepare()`.
Grafos antigos sem classe declarada continuam aceitos para bicicleta manual,
com aviso de cobertura de acesso não verificada.

Os tiles podem ser baixados pelo botão **Baixar DEM da área visível**.
A preparação e o cálculo usam arquivos locais; o download é uma ação separada.
Os tiles de mapa online são requisitados pelo navegador e podem ser desligados.

## Resultado e progresso

O app apresenta distância, tempo, subida, descida, trabalho mecânico na roda,
energia de entrada da transmissão e energia da fonte/bateria quando conhecidas.
Os presets e suas hipóteses ficam disponíveis no resultado. Para bicicleta
manual, cada rota mostra RPE médio e pico estimados na escala Borg 6–20,
além das entradas por arco em **Entradas e cálculo do RPE**. Os resultados
usam o modelo provisório existente de intensidade/resposta, sem ML treinada
ou calibração individual. A idade é registrada e não altera esse baseline.
O traçado no mapa usa o RPE estimado de cada arco, com as mesmas faixas Borg
da engine. Cinza indica ausência de RPE. Em resultados antigos sem geometria
por arco, o mapa usa a média da rota e identifica isso no tooltip. Para obter
as cores por trecho nesses resultados antigos, execute a consulta novamente.
O tooltip mostra também a inclinação média assinada pela fórmula anterior:
`100 × (subida − descida) / distância`, em %. O valor é do trecho, não acumulado
da rota inteira. Descidas preservam o sinal negativo;
altimetria incompleta aparece como indisponível, sem assumir inclinação zero.

A primeira alternativa validada pode aparecer enquanto a busca continua.
A toolkit publica previews pelo callback `on_plan`; a projeção e a validação
são as mesmas usadas no resultado final. Sem assinante, previews não são
calculados. Cancelar ou atingir o tempo limite preserva o último preview
validado, identificado como incompleto.

O limite opcional de tempo inclui preparação. O botão de cancelamento, o
heartbeat de sessão e o encerramento do subprocesso continuam disponíveis.
Limites de expansão, buckets de velocidade e completude aparecem no contrato;
concluir a fila com aproximação de estados não prova otimalidade exata.

## Candidata de ML

**Gerar uma rota por ML** vem habilitado no debug. A rede é treinada primeiro,
com até 128 arcos e duas velocidades de entrada, ou pode ser reutilizada pela
API. Seus alvos são os objetivos físicos por metro do perfil de rota escolhido:
tempo/ganho, distância/tempo, ganho/distância, combinação equilibrada ou
energia mecânica/tempo. O alvo não é o perfil de capacidade do ciclista nem RPE.

A busca neural roda antes da Pareto, com o mesmo modelo físico e os mesmos
guardrails. Ela não precisa dos resultados Pareto. Seu preview pode aparecer
primeiro. Depois, todas as candidatas são avaliadas e a política existente do
perfil decide a recomendação pelas métricas físicas. Só então os filtros de
apresentação são aplicados; o máximo limita as alternativas Pareto, com a
candidata ML adicional. Se os caminhos coincidirem, há uma rota com ambas as
origens, sem duplicação. **Gerada por ML** não significa **recomendada**.

O seletor e a legenda textual do mapa identificam a origem da rota. As cores
continuam indicando RPE. **Modelo ML e influência das features** mostra os
percentuais derivados dos pesos padronizados da rede, calculados antes das
buscas. São explicações estruturais; não substituem o custo aprendido.

O modelo é um aproximador dos objetivos da simulação (`simulation_surrogate`),
não um modelo calibrado com fisiologia observada. Não há rótulos humanos.
A validação, quando disponível, separa arcos inteiros; com poucos arcos o erro
fica indisponível. Alvos ou features constantes não ganham importância fictícia.
Estados por buckets e limites de expansão são declarados separadamente na
busca ML. Sua rota pode ser aproximada ou não estar disponível; nenhuma
otimalidade física exata é atribuída ao conjunto ampliado.

## Captura das camadas

**Inspeção por camada** captura entradas, variáveis intermediárias e saídas das
funções reais, usando `sys.monitoring` com fallback para `sys.settrace`.
São observados `MobilityEngine.plan/prepare`, `MotionEngine.traverse`,
`advance_motion`, modelos de resistência/propulsão, busca, circulação e
serialização nativa. As caches exibidas pertencem ao `MotionEngine` executado.

A camada 04 captura o baseline de intensidade/RPE quando um perfil manual é
solicitado; nas demais consultas, continua opcional.
A seção **Variáveis e pesos da ML**, na camada 04, lista as variáveis do
contrato e indica percentuais indisponíveis quando não há inferência capturada.
Quando um modelo local linear é executado sob captura, mostra todas as entradas,
as variáveis efetivamente usadas, os coeficientes e suas contribuições com sinal.
O peso percentual normaliza o módulo dos coeficientes; a participação na previsão
normaliza o módulo de `coeficiente × valor`, sem incluir o intercepto. O primeiro
depende das unidades e não é importância global; o segundo descreve apenas a
chamada amostrada, antes do recorte à escala Borg. Modelos constantes, entradas
ausentes e denominadores zero são identificados sem inventar percentuais.
Esses dados também ficam no campo `ml` dos eventos de `camadas-e2e.json`.
A captura acrescenta custo; seus tempos inclusivos não são benchmark.

É possível baixar `mobility-route-plan.json` e `camadas-e2e.json`. Resultados
antigos ainda podem ser exibidos pelo leitor de compatibilidade, sem executar
o planejador anterior. A interface alternativa `debug/dash_app.py` também usa
o mesmo adaptador da toolkit.

## Validação

```powershell
python -m pytest tests/test_stream_progress.py tests/test_debug_mobility.py tests/test_mobility_toolkit.py
```

Os testes Streamlit usam `AppTest` e executam o worker real sem abrir servidor.
