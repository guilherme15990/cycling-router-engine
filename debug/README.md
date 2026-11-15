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
4. Opcionalmente informe peso, carga ou potência em W/cv. Zero nos campos
   opcionais mantém o preset; não significa massa ou potência física zero.
5. Clique em **Executar API e capturar camadas**.

A consulta percorre toda a rede preparada, inclusive desvios fora do corredor
entre os pontos. A bbox serve à visualização; não recorta a busca.
A tela principal exercita a consulta origem–destino da toolkit. Ritmo,
fisiologia e composição de paradas da API anterior não são executados por ela.

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
Os presets e suas hipóteses ficam disponíveis no resultado. Nenhum RPE,
eficiência elétrica ou autonomia é inventado para preencher a interface.

A primeira alternativa validada pode aparecer enquanto a busca continua.
A toolkit publica previews pelo callback `on_plan`; a projeção e a validação
são as mesmas usadas no resultado final. Sem assinante, previews não são
calculados. Cancelar ou atingir o tempo limite preserva o último preview
validado, identificado como incompleto.

O limite opcional de tempo inclui preparação. O botão de cancelamento, o
heartbeat de sessão e o encerramento do subprocesso continuam disponíveis.
Limites de expansão, buckets de velocidade e completude aparecem no contrato;
concluir a fila com aproximação de estados não prova otimalidade exata.

## Inspeção das camadas

**Inspeção por camada** captura entradas, variáveis intermediárias e saídas das
funções reais, usando `sys.monitoring` com fallback para `sys.settrace`.
São observados `MobilityEngine.plan/prepare`, `MotionEngine.traverse`,
`advance_motion`, modelos de resistência/propulsão, busca, circulação e
serialização nativa. As caches exibidas pertencem ao `MotionEngine` executado.

A vista de ML/fisiologia permanece identificada como extensão opcional sem
chamadas nesta consulta. Sua ausência não representa falha da toolkit.
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
