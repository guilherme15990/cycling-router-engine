# Proposta: candidata de rota gerada por ML sem dupla contagem

**Estado:** implementação inicial disponível em `MobilityEngine.plan(ml_route=True)`.
Ela treina/reutiliza uma rede antes das buscas, com alvos físicos por metro
condicionados ao perfil de rota, gera até uma candidata neural independente
da Pareto e identifica ambas as origens no contrato e no debug. Veja o fluxo
atual em [mobility-toolkit.md](mobility-toolkit.md#geração-de-uma-candidata-de-rota-por-ml).
O texto abaixo preserva a proposta anterior de modelos de resíduos e features
adicionais para evolução; essas partes não descrevem o modo implementado.

## Papel do ML

O ML gera **uma rota candidata** sobre o mesmo grafo usado pela busca física.
Ele avalia cada arco durante a busca, considerando o trecho e o estado com que o
ciclista chega a ele. A rota proposta entra no conjunto de candidatas antes da
avaliação física completa e dos guardrails de circulação e segurança. O ML não
escolhe sozinho a recomendação final e não substitui a física.

Esse papel difere do preditor fisiológico atual: `EffortPredictor` recebe
`EffortFeatures` de uma rota já simulada. Seu RPE previsto não é um custo por
arco e não pode, sem outro contrato, orientar a expansão do grafo.

## Entradas da busca

Preservar as nove famílias de variáveis consideradas no TopoRide, com unidades,
versão e qualidade dos dados explícitas. A presença de uma variável como
entrada **não** implica acrescentar uma penalidade separada ao custo.

| Variável | Fonte possível na engine | Trabalho necessário |
| --- | --- | --- |
| Declive e rampa curta | `Segment.elevation_profile` e passos de `ArcProfile` | Extrair valores no sentido do arco, com qualidade do DEM |
| Extensão e ganho | `Arc`/`Segment` e `TraversalResult` | Reutilizar as grandezas físicas, sem uma segunda fórmula de subida |
| Classe da via | `Segment.highway` e tags OSM | Tratar como proxy; não afirmar tráfego medido |
| Vento | `Environment.headwind_mps` | Para vento direcional, acrescentar vetor e projetá-lo no rumo do arco |
| Curva | Geometria do arco de chegada e do próximo | Calcular a manobra no nó, no sentido de percurso |
| Cruzamento | Grafo e tags das vias incidentes | Definir critério versionado e distinguir dado ausente de ausência de cruzamento |
| Fadiga acumulada | `TraversalResult.rider_energy_j` por arco | Transportar trabalho do ciclista no estado da busca; `SearchState.energy_j` atual é energia mecânica |

## O que será aprendido

**Primeiro alvo proposto:** erro observado do modelo físico por trecho. Para
um percurso registrado e alinhado aos arcos, usar trabalho medido no pedivela
e duração observada:

```text
delta_energia = trabalho_observado - trabalho_fisico
delta_tempo   = tempo_observado - tempo_fisico
```

O modelo recebe as variáveis acima e prevê `delta_energia` e `delta_tempo`.
Para uma política de busca explicitamente definida por `lambda` (J/s), cada
passo usa **uma única composição**:

```text
custo = trabalho_fisico + lambda * tempo_fisico
      + delta_energia_previsto + lambda * delta_tempo_previsto
```

O mesmo efeito físico não recebe outra penalidade manual por declive, ganho,
vento ou embalo. Um limite inferior positivo por distância impede custo
negativo ou ciclos vantajosos; a primeira versão usa heurística zero até que
um limite inferior admissível seja demonstrado. `lambda` é escolha explícita
da política de rota, não um peso escondido no treino.

O contrato atual `ObservedEffort` guarda resultados de esforço de uma rota,
como RPE e frequência cardíaca. Ele **não** fornece rótulos por arco. Copiar o
RPE total para todos os arcos criaria alvos falsos. O alvo acima requer um
novo conjunto de travessias observadas, com alinhamento ao grafo, origem,
qualidade e versão. Na ausência desses dados, não há pesos aprendidos para
essa candidata; o resultado deve declarar `model_unavailable`.

Há uma alternativa executável sem medições novas: treinar um **aproximador da
física** com arcos do grafo local, como o TopoRide, e usar sua saída como o
único custo de exploração da candidata neural. Nenhuma penalidade adicional
por rampa, vento ou tráfego pode repetir sinais já presentes nesse custo.
Esse modo deve declarar `physics_surrogate`, nunca `observed_residual` ou
"calibrado com ciclistas". Ele preserva o cálculo multivariável, mas não
adiciona conhecimento externo à física. Os dois modos não devem ser
misturados silenciosamente.

Tráfego, risco e conforto não entram automaticamente em `delta_energia`.
Classe viária e cruzamentos podem ajudar a explicar atrasos observados, mas
risco ou desconforto exigem seus próprios alvos observados. Também não se
chama RPE derivado de fórmula de medição fisiológica.

## Busca e integração

1. Usar um buscador de custo aprendido separado da fronteira Pareto física.
   A poda Pareto por tempo/distância/subida pode eliminar uma rota atraente
   segundo o custo aprendido antes que ela chegue ao destino.
2. Transportar no estado ao menos nó, arco de chegada, velocidade física e
   trabalho acumulado do ciclista. A dominância deve respeitar todas as
   variáveis que alteram custos futuros; não fundir estados só pelo nó.
3. Consultar as mesmas regras de acesso e conversão e a mesma travessia física
   do motor atual. O modelo só altera o custo de exploração.
4. Produzir no máximo um caminho ML, com versão do modelo, versão das features,
   origem dos dados e custo da busca. Deduplicar por sequência de arcos se
   coincidir com uma candidata física.
5. Avaliar o caminho pela física completa e pela segurança existentes antes
   de apresentá-lo. Rejeições permanecem auditáveis. A política de seleção
   final decide entre as candidatas elegíveis; geração e seleção são operações
   distintas.

## Verificações necessárias antes de expor a função

- Com correções previstas iguais a zero, o custo ML coincide com a política
  física definida; subir uma rampa não acrescenta uma segunda cobrança.
- Num grafo pequeno em que a melhor rota aprendida não pertence à frente
  Pareto física, a busca ML ainda a encontra e a entrega como candidata.
- Acesso proibido, conversão proibida e travessia inviável nunca são aceitos
  por uma previsão favorável.
- Na falta de observações ou com versão de features incompatível, nenhum peso
  ou RPE é inventado e a ausência da candidata é explícita.
- O mesmo trecho e estado produzem o mesmo custo; o artefato de treino e a
  inferência usam a mesma extração de features.

## Decisões pendentes

1. Escolher se a primeira candidata neural será um aproximador explícito da
   física, disponível com o grafo atual, ou se aguardará um modelo de resíduos
   treinado com observações reais. Ambos usam as mesmas features e guardrails.
2. Fonte de registros de trabalho e tempo por trecho, incluindo alinhamento
   GPS→arcos e qualidade das medidas.
3. Política `lambda` e eventuais limites de desvio para a candidata ML.
4. Se conforto e segurança subjetiva terão alvos observados próprios em uma
   fase posterior.
