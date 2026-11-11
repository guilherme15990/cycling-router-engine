# 🚴 Cycling Routing Engine (Offline & Multi-Objective)

> Motor de roteamento cicloviário de alta fidelidade, **100% offline** e desenvolvido em **Python puro**, combinando simulação dinâmica longitudinal real, busca de fronteira Pareto (MOA*), validação estrita de circulação e contratos fisiológicos auditáveis.

---

### ✨ O que torna este motor diferente?

A maioria dos roteadores convencionais trata o relevo e o esforço como penalidades estáticas de peso em uma aresta. Este motor modela a travessia como um **problema físico e dinâmico real**:

- **Física Longitudinal Real:** Modela aceleração, desaceleração, atrito de rolamento ($C_{rr}$), arrasto aerodinâmico quadrático ($C_d \cdot A$), gradiente altimétrico e dissipação de energia por frenagem. A velocidade de saída de um trecho é propagada como velocidade de entrada do próximo trecho.
- **Busca Multiobjetivo (A* Pareto):** Em vez de reduzir múltiplos objetivos a um número arbitrário, o motor explora a fronteira de Pareto (tempo, distância, ganho altimétrico e energia mecânica) com dominância discretizada por velocidade (`SpeedBucketDominance`).
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
