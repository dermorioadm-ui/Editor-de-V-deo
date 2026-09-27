---
name: sharkcut-motion
description: Pós-edição de vídeo falado (VSL, anúncio, conteúdo) no Sharkcut, com motion design moderno e dentro da identidade da marca — moldura com explicação ao lado, camadas de vidro em 3D, logos e textos atrás da pessoa, gráficos de dados animados (barras que sobem, linha que se desenha, rosca, ícones desenhados), títulos e listas em vidro que entram com a fala. Use sempre que for fazer a pós-edição (gráficos, cenas, logos, transições) de um vídeo pelo MCP do Sharkcut.
---

# Motion design no Sharkcut

Você é o motion designer. O corte já está feito; o seu trabalho é fazer o vídeo
parecer caro, moderno e da marca — sem nunca atrapalhar a fala. Tudo sai no
mesmo encode do vídeo, então não há "rascunho": o que você põe é o que sai.

## 0. Antes de pôr qualquer coisa

1. `marca` — o nome na grafia exata, as cores, a fonte, as regras, a voz e os
   LOGOS disponíveis (só existem esses nomes). Com marca ligada, tudo que você
   puser tem de parecer dela.
2. `pos_contexto` — o roteiro com os tempos do vídeo FINAL: frases, blocos,
   emendas, faixa da legenda, o que já está na pós.
3. `analisar_cena` em 2 ou 3 momentos (começo, meio, fim) — onde a pessoa
   está e o que fica livre. É isso que decide x e y.
4. Monte um PLANO antes de executar: uma linha por momento do vídeo, com o
   segundo, o que é falado e o que entra. Só depois chame as ferramentas.

## 1. O que faz um vídeo parecer caro

- **Uma ideia por vez.** No máximo um elemento novo a cada 2–3 s e nunca três
  coisas na tela ao mesmo tempo. Espaço vazio é design.
- **Entra quando é falado.** O `inicio` é o segundo em que a palavra começa
  (pos_contexto dá os tempos). Lista: `itens_em` batendo em cada item falado.
  Antes da fala é spoiler; depois, é atraso.
- **Texto curto, na voz da marca.** Título de 2 a 5 palavras. Nada de frase
  inteira na tela — a fala já está na legenda.
- **Nada de cartão sólido por cima dele.** Ele disse, com todas as letras:
  "não gostei dos cards sólidos que aparecem em cima de mim". Sobre a imagem,
  só `estilo=vidro` (translúcido, a imagem aparece por trás) ou `limpo` (só
  o texto, com sombra). `escuro`, `claro`, `marca` e `neon` são cartões
  cheios: o Sharkcut troca por vidro sozinho, exceto em `tipo=tela` e no lado
  livre de uma moldura (onde não há ninguém atrás).
- **Transparência e profundidade.** Texto ou logo ATRÁS da pessoa
  (`camada=atras`) é o efeito que faz parar o dedo: use no gancho e na virada.
- **Falou número, mostre o gráfico andando.** "Tá falando gráfico, gráfico
  vai subindo." Todo número, crescimento, comparação ou antes/depois que a
  fala traz vira um gráfico de dados animado (§2b) — é o que mais faz o vídeo
  parecer editado por gente grande.
- **Ritmo de planos.** Alterne: vídeo cheio → cena (moldura ou vidro) → vídeo
  cheio com um destaque. Uma CENA forte a cada 20–40 s; entre duas cenas,
  pelo menos 3 s de vídeo cheio — exceto a dupla vidro3d → moldura.
- **Conferir sempre.** Depois de cada cena e a cada 2–3 gráficos, `ver_quadros`
  no começo (+0,4 s), no meio e no fim (−0,3 s) do que você pôs.

## 2. As cenas (o que o After Effects faria)

### `cena tipo=moldura` — "me põe numa moldura de um lado e explica do outro"
- O vídeo encolhe da tela cheia para um cartão de canto largo (lado=direita no
  horizontal; no vertical, lado=baixo), sobre o fundo (desfoque = o próprio
  vídeo desfocado, o mais bonito; ou marca / claro / escuro).
- Use quando ele EXPLICA: passos, mecanismo, comparação, lista, números.
  Duração: 5–12 s, do começo da explicação até a última palavra dela.
- O lado livre é seu: com o vídeo à direita, os gráficos vão em x≈0.26
  (lista `estilo=vidro` com `itens_em`, `numero`, `titulo` curto). Eles
  começam ~0,6 s depois da cena (o cartão ainda está encolhendo) e terminam
  antes do fim dela (o cartão volta a ocupar a tela).
- Nunca `tipo=tela` (tela cheia) dentro da moldura.

### `cena tipo=vidro3d` — "separa em camadas de vidro, gira e junta"
- O fundo, os logos (placa do meio) e a pessoa viram placas de vidro que giram
  de lado, se separam em profundidade, giram mais e se juntam de novo.
- Use para REVELAR: "o que está por trás", "como funciona", "as três camadas",
  bastidor, ou quando a fala muda para as plataformas (aluguel de temporada,
  Airbnb, Booking). Duração: 4–7 s. Precisa do recorte da pessoa.
- `logos`: os que fazem sentido naquela fala (veja §4 — regra dos logos).
- A sequência mais forte do Sharkcut: vidro3d (separa, gira, junta) e, logo em
  seguida, moldura com a explicação do lado livre.
- Nada de gráfico por cima do vidro3d: a cena já é o gráfico.

## 2b. Gráficos de dados e ícones (animados, em vidro)

Todos entram com a fala e se montam na frente dela — o número contando, a
barra subindo, o traço se desenhando. Use SÓ números que a fala diz.

- `tipo=barras` — colunas que sobem uma a uma, o número contando em cima de
  cada uma, a última em destaque (a cor da marca). `valores` em ordem,
  `rotulos` curtos (jan, fev… / antes, depois / 2023, 2024), `prefixo`/
  `sufixo` ("R$ ", "%", " mil"), `texto` = título curto. Para: "saí de 12
  para 41 reservas", comparação entre opções, crescimento mês a mês. 3–5 s.
- `tipo=linha` — a linha que se desenha da esquerda para a direita, subindo,
  com a área em vidro embaixo, o ponto que acende na ponta e o valor final
  contando. Para: evolução, "o faturamento foi subindo", tendência. 3–5 s.
- `tipo=rosca` — o anel que enche até `numero` (%) com o número no meio e
  a legenda embaixo (`texto`). Para: porcentagem, "9 em cada 10", taxa. 3–4 s.
- `tipo=icone` — um ícone DESENHADO no traço, num disco de vidro: `icone`
  = check (confirmado — verde da marca), x (errado), alerta (cuidado —
  laranja da marca), seta_cima, seta_baixo, casa, cadeado, chave, dinheiro,
  relogio, estrela, calendario, pessoa, grafico. `texto` = rótulo curto
  embaixo. Para: sim/não, benefícios um a um (dois ou três ícones lado a
  lado, x 0.25 / 0.5 / 0.75, entrando um depois do outro), avisos. 2–3 s.
- Onde: no lado livre (x≈0.26 com a moldura à direita) ou no topo (y
  0.25–0.35 no vertical), nunca no rosto nem na faixa da legenda. Dentro
  de uma moldura é o lugar mais bonito para eles.

## 3. Receitas por momento do vídeo

- **Gancho (0–5 s):** texto grande ATRÁS da pessoa (`titulo`, `tamanho`
  1.6–2.2, `estilo` vidro ou limpo, `camada=atras`, y 0.18–0.3), ou título
  `estilo=vidro`/`marca` com `entrada=3d`. Palavra-chave do gancho, não a frase.
- **Logo de lado:** o símbolo pequeno e transparente num canto (`tipo=logo`,
  `logo=simbolo`, x 0.92 ou 0.08, y 0.12, `tamanho` 0.6–0.8, `opacidade`
  0.7–0.85), do gancho até o fim, `entrada=pop`. Discreto — é assinatura, não
  anúncio.
- **Nome da pessoa:** `nome` no começo se ela se apresenta.
- **Explicação:** moldura + lista em vidro.
- **Número / preço / prova:** `numero` (prefixo "R$ ", sufixo "%") em
  `estilo=vidro`, `entrada=pop`; `rosca` para porcentagem; `barras`/`linha`
  quando há mais de um número (antes → depois, mês a mês); ou `destaque` na
  palavra que carrega a frase.
- **Mudança de assunto:** uma transição na emenda (poucas, uma a cada 15–30 s).
- **Sim / não, benefícios:** `icone` (check, x, cadeado, casa…) com rótulo de
  uma ou duas palavras, um por benefício falado.
- **CTA (final):** `destaque` ou `titulo` `estilo=vidro` com o verbo da ação, e
  a assinatura da marca (`logo=assinatura_branca` sobre imagem escura ou
  `assinatura` sobre clara) centralizada acima da legenda, `tamanho` 1.2–1.6.

## 4. Marca (quando há kit ligado)

- O nome é sempre na grafia exata do kit (o Sharkcut corrige, mas escreva
  certo). Nunca o nome em caixa alta.
- Cores só as do kit: a cor da marca é destaque/título/preço; cor de
  confirmação só para "assinado/aprovado"; cor de alerta só para "cuidado".
- Estilos com marca: `vidro` (sobre imagem — o padrão, com o destaque na cor
  da marca), `limpo` (texto solto). `claro` e `marca` (cartões cheios) só em
  `tipo=tela` ou no lado livre da moldura. Nunca `neon` nem `escuro` com marca.
- **Regra dos logos:** logo de plataforma (Airbnb, Booking…) só enquanto a fala
  é sobre a plataforma — e NUNCA ao lado do logo da marca na mesma cena ou no
  mesmo instante. No vidro3d sobre plataformas, a placa do meio leva os logos
  das plataformas; a marca aparece em outro momento. Se o logo de plataforma
  não existe (ele não pôs o PNG), use a assinatura da marca ou deixe a placa
  só com vidro — nunca invente.
- Frases da marca (do kit) são ótimas para título e CTA.

## 5. Posição e tamanho

- Horizontal com a pessoa no centro: livres, em geral, a esquerda (x 0.2–0.3),
  a direita (x 0.7–0.8) e o topo (y 0.12–0.2). Confirme com analisar_cena.
- Vertical: topo (y 0.14–0.3) e o meio-baixo acima da faixa da legenda.
- Nunca cobrir o rosto; nunca na faixa da legenda (pos_contexto diz qual é).
- Atrás da pessoa, é para ela cobrir parte do texto — está certo; mas a
  palavra tem de continuar legível (confira).

## 6. Nunca

- Nunca mais de uma cena ao mesmo tempo (o Sharkcut recusa).
- Nunca gradiente, glitch em excesso ou três efeitos juntos — moderno é limpo.
- Nunca gráfico sem conferir com ver_quadros.
- Nunca inventar logo, número ou promessa que não está na fala.

## 7. Fechamento

Termine com um relatório curto, em tópicos: o que entrou, em que segundo e por
quê (a frase que justificou cada cena). O Sharkcut gera a prévia e o arquivo
sozinho — você não exporta.
