# Debug E2E (Streamlit)

Aplicativo de depuração que consome a **API E2E** do motor:

```python
from router.integration import plan_routes

plan = plan_routes(
    graph,
    origin,
    destination,
    cyclist=cyclist,
    bicycle=bicycle,
    profile="fastest",
)
```

A interface principal é o **Consulta**: escolher os pontos **clicando no mapa**
(ou digitando as coordenadas), ajustar ciclista/bicicleta e ver o resultado. Os
logs ficam em uma **aba separada** (`Logs`), com filtro por nível, busca,
download e limpeza.


## Bancada de inspeção das camadas

1. Carregue o grafo e monte o cenário na aba **Consulta**: pontos, paradas,
   perfil de rota, ciclista e bicicleta.
2. Ajuste ambiente, velocidade inicial, passo da simulação, snapping,
   expansão da zona e margem do bbox. Para experimentar predição local,
   informe o diretório do `ModelStore`, a versão do artefato e, se necessário,
   o perfil fisiológico. A API não treina um modelo automaticamente.
3. Mantenha **Capturar entradas, cálculos e saídas das camadas** habilitado e
   clique em **Executar API e capturar camadas**.
4. Em **Inspeção por camada**, escolha uma das seis camadas e uma chamada
   capturada. Confira **Entrada**, **Cálculos intermediários**, **Saída** e
   **Implementação executada**. A linha do tempo preserva a ordem real de
   entrada das chamadas, inclusive quando uma camada chama outra.
5. Baixe `camadas-e2e.json` para guardar o cenário e os diagnósticos. O
   `route-plan.json` completo continua disponível em Consulta.

A captura acompanha a chamada real de `router.integration.plan_routes` com
`sys.monitoring` quando disponível (com fallback para `sys.settrace`); não substitui funções nem reimplementa
o algoritmo. Também observa a serialização pública. As seis vistas cobrem
dados/snapping/bbox, física/matemática, perfis/busca, ML/predição,
avaliação/seleção e contrato/resultado. As funções instrumentadas são
explicitadas em `debug/layer_trace.py`.

As chamadas instrumentadas são contadas. Por padrão, uma amostra de cada
função tem snapshot, e coleções grandes são
truncadas com contagem explícita. Os tempos são inclusivos, com custo de
depuração, e não representam benchmark. Uma camada sem chamada observada
não é automaticamente considerada ausente. Exceções tratadas pelo motor
aparecem como observações; a falha externa da API aparece separadamente.
Se houver um debugger Python ativo, desligue a captura ou o debugger.

A API publica a primeira rota validada enquanto a busca continua. O mapa mostra o resultado parcial e atualiza alternativas conforme são encontradas. A inspeção contém amostras das chamadas, não uma animação nem execução pausada entre etapas.

## Escolher os pontos no mapa

O mapa fica no topo da aba **Consulta** e aparece **antes** de carregar o grafo
(nesse caso usa o centro da área do PBF padrão). Escolher os pontos:

1. escolha se o clique define a **Origem** ou o **Destino**;
2. clique no mapa — o ponto aparece na hora e a coordenada vai para o campo
   correspondente (`Origem (lat, lon)` / `Destino (lat, lon)`);
3. depois de escolhido, **arraste o marcador** para ajustar a posição;
4. use **Inverter** para trocar origem/destino ou **Limpar pontos** para zerar;
5. depois de calcular, a rota aparece desenhada no mesmo mapa e o seletor de
   rota atualiza o traçado.

Sem grafo carregado, use **Carregar grafo em cache** (logo abaixo do mapa) para
carregar `debug/.cache/graph_v4.json`; os pontos já escolhidos são preservados.

O mapa é um componente Leaflet bidirecional (`st.components.v2`) com tiles do
OpenStreetMap carregados pelo navegador; o Python/motor continua sem rede.
Desmarque **Mapa com tiles (online)** para ver apenas o traçado offline.

## Altimetria (DEM local)

A opção **Vias por declividade (DEM local)** consulta a API
`router.integration.altimetry_edges(graph, bbox)` para a área visível no mapa.
A função retorna vias, inclinações e faixas de cor compatíveis com o TopoRide:
verde até 4%, laranja até 8% e vermelho acima de 8%. Cinza representa perfil
incompleto ou indisponível. O retorno informa o total, a quantidade sem perfil e
se houve amostragem por limite de desenho.

**Baixar DEM da área visível** busca tiles Terrarium em segundo plano, em zoom
12, com limite de 40 tiles por solicitação. A API reutiliza arquivos já
existentes em `data/dem`. Para usar as altitudes na busca, na física e na
camada de vias, aplique **Aplicar altimetria do DEM local** no painel lateral;
o download não modifica automaticamente o grafo. A operação pode regravar o
arquivo do grafo carregado. Não há fallback automático para OpenTopography.

A API pública `prepare_dem_tiles(bbox, tiles_dir, zoom=12)` oferece a mesma
preparação para outros consumidores. Seu bbox usa `(sul, oeste, norte, leste)`;
a chamada acessa a rede somente quando faltam tiles. A leitura do DEM e a
consulta das vias permanecem offline. A área disponível depende dos tiles
locais e o motor mantém ausência de altitude como dado ausente.

## Requisitos

- Python 3.10+
- Streamlit (veja `requirements.txt`)

```powershell
cd "C:\Users\sus\Documents\$apps\roteamento com ml"
python -m pip install -r debug/requirements.txt
```

O app usa apenas a biblioteca padrão + Streamlit (para a camada de altimetria
usa `Pillow` e `numpy`, que já são dependências do próprio Streamlit). Gráficos
são Vega-Lite puro (`st.vega_lite_chart` com os dados dentro de cada camada); se
o ambiente não conseguir renderizar, a mesma informação aparece em tabela
Markdown. Nenhum widget depende de `pandas`/`pyarrow`.

O mapa é um componente Leaflet bidirecional (`st.components.v2`), com Leaflet e
tiles do OpenStreetMap carregados **pelo navegador** — o motor e o Python
continuam sem acesso à rede. Em Streamlit sem `components.v2`, o app cai para um
mapa estático (`st.iframe`), sem clique. Desmarque **Mapa com tiles (online)**
para ver apenas o traçado offline.

## Executar

A partir da raiz do repositório:

```powershell
python -m streamlit run debug/app.py
```

O `app.py` adiciona `src/` ao `sys.path`, então funciona direto do checkout,
sem instalar o pacote.

## Grafo

O app espera um grafo no formato `roteamento.cycling-graph` **versão 4**.
Os arquivos `data/se.json`, `data/se_raw.json` e `data/query.json` são legados
(v1/v2) e não são aceitos pelo `load_graph` atual.

Se não houver um grafo v4, use o painel lateral:

1. abra **Construir grafo v4 a partir de PBF**;
2. confirme o PBF local
   (`data/sources/planet_-51.73_-17.379_ff78d815.osm.pbf`);
3. o grafo simplificado será salvo em `debug/.cache/graph_v4.json` e
   carregado automaticamente;
4. se houver tiles em `data/dem`, a altimetria do DEM é aplicada ao grafo
   durante o build.

O `debug/.cache/` é ignorado pelo Git. A construção é local e não acessa a
rede.

## O que a aba Consulta mostra

- métricas do contrato (`distância`, `tempo`, `subida`, `descida`, energia);
- RPE médio e pico de RPE (estimativa provisória, Borg 6–20);
- estado de segurança e avisos de truncamento;
- mapa da rota com **Leaflet + tiles do OpenStreetMap** (marcadores de origem e
  destino, link para abrir no OSM), **vias coloridas por declividade** do DEM local e
  perfil de elevação;
- distribuição de intensidade por distância e por tempo (`i1`..`i5`);
- tabela da geometria (`dist_m`, velocidade, rampa, RPE, faixa, watts);
- contrato público completo em JSON para download.

A aba **Logs** registra carregamento de grafo, parâmetros da consulta, chamada
E2E, tempo de execução, avisos e erros com traceback.

## Escopo

Este diretório é apenas de depuração: não é empacotado, não é descoberto pelos
testes do projeto e não altera o motor.
## Recuperação e limites da bancada

As consultas são executadas por `layer_worker.py` em processo separado.
O Streamlit acompanha a execução sem bloquear o formulário e oferece
**Cancelar consulta**. O limite inicial é zero (sem limite de tempo), configurável no
formulário. Se definido, o próprio worker aplica esse limite mesmo sem navegador conectado.
O tempo inclui a leitura do grafo de arquivo. Os resultados continuam vindo
da mesma API, com inspeção das seis camadas.

A captura usa uma amostra por função por padrão, sem contar cada chamada
posterior dos cálculos físicos. Snapshots têm limite de tamanho. Esses
contadores representam amostras, não o total de chamadas do algoritmo.
Consultas canceladas ou encerradas pelo limite preservam o último plano validado, identificado como parcial.
Se o navegador for fechado durante uma consulta, o worker encerra o cálculo
após 30 segundos sem heartbeat; isso evita processos órfãos sem limitar
consultas ativas.

## Busca progressiva

A consulta expande a zona até cobrir o grafo carregado; a cada expansão uma
nova busca é iniciada e, dentro dela, a fila é preservada entre atualizações.
A primeira rota e as alternativas são exibidas depois da simulação completa e
da avaliação de segurança. Com paradas, só são exibidos trajetos com todas as
pernas válidas. O cancelamento mantém a última rota parcial.

A conclusão cobre apenas o grafo fornecido e não é uma prova de otimalidade
global. O crescimento geográfico termina na cobertura do grafo; a busca
multiobjetivo dentro de uma zona ainda não tem prova geral de terminação para
estados físicos contínuos. Não se configura limite fixo de labels; use
Cancelar ou um tempo opcional se necessário. Veja `docs/reference/progressive.md`.
