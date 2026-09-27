# Direção criativa com Claude ou Codex

O diretor decide o que a edição precisa comunicar, em que momento e por quê.
Ele lê a fala e a marca, monta um plano com tempos do vídeo final, trabalha
na montagem e inspeciona quadros renderizados pelo mesmo motor da exportação.

## Usar sua conta do ChatGPT

1. Instale o Codex CLI ou use o executável que acompanha o app Codex.
2. No PowerShell, execute `codex login` e entre com a conta do ChatGPT.
3. Na primeira tela do Sharkcut, escolha **Codex · ChatGPT** em **Quem edita
   este vídeo**, em **Quem dirige o acabamento**, ou nos dois.
4. Confira **Conta do ChatGPT conectada**, escolha a linguagem e descreva o
   resultado desejado. O modelo é o padrão do Codex, a menos que você informe
   um identificador disponível na sua conta em **Caminho e modelo**.

Não exige uma chave da API OpenAI. A execução usa a assinatura e seus limites.
Precisa de uma versão do CLI com `--ignore-user-config`. Login, cobrança e
disponibilidade de modelos pertencem ao Codex/ChatGPT:
[autenticação](https://learn.chatgpt.com/docs/auth),
[execução sem interação](https://learn.chatgpt.com/docs/non-interactive-mode).

O arquivo fonte e a renderização ficam no computador. A transcrição, as
instruções e os quadros que a IA consulta são enviados ao provedor escolhido.

## Dois momentos, escolhas independentes

| Etapa | Responsabilidade |
| --- | --- |
| Edição inicial | Revisar fala, redundância, pausas, ritmo, cortes e legenda. |
| Acabamento | Composição, títulos, comparação, gráficos, cenas e transições; ferramentas de recorte ficam indisponíveis. |

Pode usar Codex no corte e Claude no acabamento, ou o inverso. Com o mesmo
diretor nas duas etapas, uma execução completa mantém o contexto. Projetos
antigos continuam respeitando a opção `pos_claude` até receberem uma escolha
nova. **Só a edição** desliga explicitamente a pós automática.

Em projeto já aberto, **Pós → Direção criativa** permite dirigir o acabamento,
revisar os cortes ou executar a edição completa. **Plano e decisões** mostra
a intenção de cada momento e permite saltar para ele na timeline.

## Briefing que ajuda a direção

“Este vídeo explica um mecanismo para quem ainda não conhece o produto.
Preserve as pausas que criam expectativa. Na explicação, revele cada passo
quando ele for dito. Mostre a comparação só quando a segunda opção aparecer.
Deixe a pessoa em tela durante a conclusão. Evite repetir o texto da legenda.”

Os perfis são orientações editoriais para a IA, e não filtros aplicados a todo
o vídeo: **editorial** busca hierarquia e precisão; **cinema** dá mais respiro;
**dinâmico** trabalha contraste de ritmo. Cada intervenção deve esclarecer,
demonstrar ou conduzir o olhar. Nenhuma cota de efeitos substitui essa decisão.

## Recursos novos do motor

- `entrada=cinema`: aproximação discreta, foco e opacidade chegando juntos.
- `entrada=linhas`: título revelado linha por linha, mantendo o alinhamento.
- `tipo=comparacao`: duas colunas com rótulos e entradas independentes.
- `estilo=editorial`: texto sem contorno, indicado para fundo escuro e estável.
- Curvas `cinema` e `organica` em keyframes, iguais na prévia e na exportação.
- Medição da fonte com Pillow quando disponível, com aproximação como fallback.
- `compor`: receitas editáveis de gancho, comparação, passos, prova e fechamento.

Exemplo de comparação pelo MCP, com tempos do vídeo final:

```json
{
  "projeto": "ID_DO_PROJETO",
  "tipo": "comparacao",
  "inicio": 12,
  "fim": 18,
  "titulo": "DUAS POSSIBILIDADES",
  "rotulos": ["PRIMEIRA", "SEGUNDA"],
  "itens": [
    {"texto": "Ideia apresentada primeiro", "em": 0.4},
    {"texto": "Alternativa explicada depois", "em": 2.2}
  ],
  "estilo": "vidro",
  "x": 0.5,
  "y": 0.25
}
```

`itens.em` é relativo ao começo da composição. Escolha a posição depois de
consultar a cena: a receita não sabe sozinha onde está o rosto. O resultado é
um gráfico normal do Sharkcut, ajustável ou removível na interface. Itens
registram a autoria `claude`, `codex` ou manual.

## O que a conferência garante

`direcao planejar` registra objetivo, linguagem e momentos. `direcao ler`
informa os tempos a conferir. `ver_quadros` renderiza até seis imagens por
chamada; peça novas chamadas para os demais momentos. Só imagens sem avisos
de renderização contam. Depois, `direcao revisar` registra o parecer.

A revisão fica ligada às decisões, palavras, correções e marca daquela
montagem. Alterá-las exige nova conferência. Gerar a prévia não invalida a
revisão por simples troca dos IDs internos das legendas automáticas. Uma
execução que termina sem concluir essa etapa aparece como incompleta e não
dispara a finalização automática.

Isso comprova a geração dos quadros e registra a avaliação da IA; não mede
bom gosto nem certifica movimento, cortes ou som contínuo. Assista à prévia
com áudio antes de publicar. Falta de login, limites da conta e recusa de
permissão aparecem como erro, sem declarar uma edição concluída.

## Isolamento e testes

O Codex recebe somente as ferramentas do Sharkcut daquela etapa, com chave
temporária vinculada ao projeto. Shell, apps, web e agentes paralelos ficam
desligados. A revisão automática de permissões do Codex permanece ativa.
As configurações pessoais não são modificadas nem as credenciais copiadas.

```powershell
python -m unittest tests.diretor -v
python -m tests.mcp
cd frontend
npm ci
npm run typecheck
npm run build
```

Os testes novos usam dados sintéticos em pasta temporária. A suíte MCP
existente deve rodar com `EDITOR_DATA_DIR` e `EDITOR_OUTPUT_DIR` temporários.
Teste opcional que **consome a conta conectada**, também com vídeo sintético:
`python -m tests.codex_live --usar-conta`.

Estudo visual reproduzível, sem IA e sem material pessoal:
`python -m exemplos.estudo_direcao --saida caminho/estudo`.
