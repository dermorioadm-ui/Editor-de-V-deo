# Motor criativo do Sharkcut

O objetivo é dar ao diretor Claude/Codex peças que ele possa combinar para
comunicar cada ideia. As artes vetoriais pertencem ao motor do Sharkcut. O
Blender é uma integração opcional com software livre instalado no computador.
Nenhum dos dois exige serviço de render pago ou chave de API. As contas de IA
continuam sujeitas aos limites das assinaturas já usadas pelo editor.

## O que esta versão acrescenta

- Composição vetorial livre: texto, números, retângulos arredondados, elipses
  e traçados, em grupos que se movem juntos ou independentemente.
- Keyframes de posição, escala, rotação, opacidade, dimensões, valor numérico,
  revelação de traço e transformação entre duas geometrias compatíveis.
- Curvas de movimento, entradas escalonadas, fonte e cores da marca. Pode
  compor atrás da pessoa usando o recorte que o Sharkcut já possui.
- Exemplos editáveis de tipografia, mecanismo, órbita e gráfico. A IA pode
  criar uma estrutura completamente diferente com os mesmos elementos.
- Blender: cubos, esferas, anéis, cilindros, planos e texto extrudado;
  materiais, iluminação de estúdio, câmera e objetos animados. Render local
  em CPU, com transparência, cache, progresso e cancelamento.
- Ferramentas MCP `arte` e `arte_3d` para os dois diretores. O resultado fica
  na timeline; a revisão aguarda renders 3D pendentes antes de ser aprovada.

## Como usar

Depois de instalar esta versão, abra **Pós → Artes e composição**. Os quatro
botões criam exemplos no cursor. No inspetor é possível ajustar os textos,
posição, tamanho, cor, duração e colocar a composição atrás da pessoa.
**Ver o quadro exato** usa o mesmo renderizador da exportação. Para conferir
o movimento, gere a prévia do vídeo.

Ao pedir uma edição ao diretor, descreva o efeito e seu propósito:

> Na explicação do mecanismo, desenhe um diagrama de três partes. Revele as
> conexões quando cada relação for dita. Use a fonte e a cor da marca,
> mantenha o rosto e as legendas livres e retorne ao vídeo cheio na conclusão.

> Mostre a evolução desse número numa linha animada. Os valores precisam
> corresponder aos dados que forneci. Deixe o resultado parado por tempo
> suficiente para ler.

Os números dos modelos são ilustrativos. O diretor deve substituí-los por
dados reais da fala ou do briefing, sem inventar resultados.

### Blender opcional

Instale o Blender pelo [site oficial](https://www.blender.org/download/).
O Sharkcut procura o executável no PATH e nas pastas usuais da instalação.
Para uma cópia portátil, defina `EDITOR_BLENDER` com o caminho absoluto para
`blender.exe` antes de iniciar o editor. A aba Pós informa se foi encontrado.

O diretor consulta `arte acao=catalogo` e chama `arte_3d acao=criar` com uma
cena declarativa. O programa usa seu próprio script; não executa código
arbitrário recebido do modelo. Depois de terminar, o vídeo com alfa entra
como sobreposição, ajustável na aba Mídia. O arquivo `.blend` da cena fica
guardado junto do render, na pasta `artes-3d` do projeto.

A integração não inclui o Blender dentro do ZIP. O teste desta versão usou
Blender 4.5.9 portátil, obtido do servidor oficial e conferido por SHA-256.
Não foi alterada a instalação principal do editor.

## Contrato da composição nativa

`arte acao=criar` recebe `projeto`, `inicio`, `fim`, um `nome` e `composicao`.
Para substituir uma composição, use `acao=atualizar` e seu `id`.

```json
{
  "tela": [1000, 1000],
  "elementos": [
    {"id": "grupo", "tipo": "grupo", "x": 500, "y": 500},
    {"id": "anel", "tipo": "elipse", "pai": "grupo",
     "largura": 260, "altura": 260, "cor": "nenhuma",
     "contorno": "marca", "espessura": 5,
     "marcos": [{"t": 0, "escala": 0.5, "opacidade": 0},
                {"t": 0.8, "escala": 1, "opacidade": 1, "curva": "saida"}]},
    {"id": "texto", "tipo": "texto", "pai": "grupo",
     "texto": "IDEIA", "corpo": 46, "cor": "texto"}
  ]
}
```

Os tempos internos são relativos ao início da composição. Pais precisam
aparecer antes dos filhos. A prancheta se encaixa no formato sem distorcer;
`x`, `y` e `tamanho` externos posicionam a arte inteira. O traçado usa
`pontos=[[x,y],...]`, `progresso=0..1`, `pontos_fim` com a mesma quantidade de
pontos e `morph=0..1`. Esses dados continuam editáveis no projeto.

Limites deliberados desta primeira versão: 48 elementos, 40 marcos por
elemento, 60 segundos por composição; animação vetorial amostrada em 30 Hz.
Blender: 24 objetos, 10 segundos e até 1920×1080 pixels equivalentes. O custo
de tempo depende da máquina; o render usa CPU para não exigir uma GPU.

## Próximas prioridades

1. **Tracking e máscaras livres:** prender anotações a objetos/superfícies,
   substituir uma tela, desenhar e refinar recortes que acompanham o vídeo.
2. **Assets dentro das composições:** imagens, SVG e modelos GLB com edição
   de seus elementos e transformações, ampliando a arte a partir de arquivos.
3. **Direção de som e acabamento:** efeitos sonoros pontuais, coerência de
   cor entre tomadas e decisões de ritmo, preservando a preferência pela
   música constante quando escolhida.
4. **Revisão temporal:** inspeção de sequências e áudio com evidências reais,
   além da revisão de quadros estáticos já disponível. Isso depende também
   do que o cliente e o modelo escolhido conseguem receber e analisar.

Essas quatro frentes ainda não estão implementadas por esta entrega. Também
não há importação MOGRT, Lottie/Rive, rotoscopia livre, tracking de câmera,
simulação física, rigging ou importação de modelos 3D externos.

## Verificação e mostruário

```powershell
python -m unittest tests.artes tests.diretor -v
python -m tests.blender_live --saida PASTA_DE_TESTE
python -m exemplos.estudo_artes --saida PASTA_DO_MOSTRUARIO --blender
```

O teste do Blender usa mídia sintética e banco próprio. Confere quadros,
transparência, movimento de objetos/câmera e exportação pelo Sharkcut. O
mostruário é um estudo visual sem áudio, sem gravação pessoal e sem IA.
Mostra recursos; não substitui avaliar ritmo e acabamento num vídeo real.
