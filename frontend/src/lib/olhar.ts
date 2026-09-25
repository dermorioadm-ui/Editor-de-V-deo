// ONDE O OLHO ESTÁ OLHANDO, EM GRAUS.
//
// O problema do teleprompter é geométrico antes de ser tecnológico: você lê o
// texto, o texto está num lugar da tela, a lente está em outro, e a diferença
// entre os dois é um ÂNGULO. Quem assiste não vê "o texto"; vê o desvio.
//
// A tecnologia que redesenha o olho (NVIDIA Broadcast, Apple) existe e é boa,
// mas ela conserta o sintoma. O ângulo é a causa, e ele se resolve movendo o
// texto — de graça, sem GPU, sem modelo nenhum.
//
// A conta é trigonometria de colégio, e o que a torna útil é ter os números
// certos: o tamanho FÍSICO do pixel (que sai da diagonal da tela) e a
// distância do olho até a tela.

/** Limiares em graus. Não são chute: são o que se vê. */
export const IMPERCEPTIVEL = 4   // abaixo disto ninguém nota
export const LEVE = 8            // entre 4 e 8 parece "olhando para a tela"
                                 // acima de 8 parece "lendo alguma coisa"

export type Medida = {
  /** o desvio em graus */
  graus: number
  /** o mesmo desvio em centímetros na tela */
  centimetros: number
  veredito: 'imperceptivel' | 'leve' | 'aparece'
  recado: string
}

/**
 * A altura FÍSICA da tela, em centímetros, a partir da diagonal em polegadas.
 *
 * A diagonal sozinha não dá a altura: uma tela de 24" 16:9 tem 29,9 cm de
 * altura e uma de 24" 4:3 tem 36,6 cm. Por isso a proporção entra na conta.
 */
export function alturaDaTelaCm(diagonalPolegadas: number, proporcao: number): number {
  const diagCm = Math.max(1, diagonalPolegadas) * 2.54
  const r = Math.max(0.2, proporcao)
  return diagCm / Math.sqrt(1 + r * r)
}

/**
 * O desvio do olhar, dado onde está a lente e onde está a linha de leitura.
 *
 * `lenteY` e `linhaY` são frações da altura da JANELA (0 = topo). A lente de
 * notebook fica acima da borda de cima, então o padrão dela é 0 — e um pouco
 * negativo quando a moldura é grossa.
 */
export function desvioDoOlhar(args: {
  lenteY: number
  linhaY: number
  alturaJanelaPx: number
  diagonalPolegadas: number
  proporcaoTela: number
  distanciaCm: number
}): Medida {
  const alturaCm = alturaDaTelaCm(args.diagonalPolegadas, args.proporcaoTela)
  const cmPorPx = alturaCm / Math.max(1, args.alturaJanelaPx)
  const distanciaPx = Math.abs(args.linhaY - args.lenteY) * args.alturaJanelaPx
  const centimetros = distanciaPx * cmPorPx
  const graus = Math.atan2(centimetros, Math.max(1, args.distanciaCm)) * 180 / Math.PI
  const veredito = graus < IMPERCEPTIVEL ? 'imperceptivel'
    : graus < LEVE ? 'leve' : 'aparece'
  const recado = veredito === 'imperceptivel'
    ? 'ninguém vai notar que você está lendo'
    : veredito === 'leve'
      ? 'parece que você está olhando para a tela, não para a lente'
      : 'vai dar para ver que você está lendo — suba a linha de leitura'
  return { graus: Math.round(graus * 10) / 10,
           centimetros: Math.round(centimetros * 10) / 10,
           veredito, recado }
}

/**
 * Onde pôr a linha de leitura para o desvio ficar dentro de um limite.
 *
 * Devolve a fração da janela. É o "colar na lente": em vez de o usuário
 * arrastar até acertar, o programa calcula a maior distância que ainda cabe
 * no ângulo pedido — porque texto colado demais na borda fica difícil de ler.
 */
export function linhaParaOAngulo(args: {
  lenteY: number
  alturaJanelaPx: number
  diagonalPolegadas: number
  proporcaoTela: number
  distanciaCm: number
  grausAlvo?: number
}): number {
  const alvo = args.grausAlvo ?? IMPERCEPTIVEL
  const alturaCm = alturaDaTelaCm(args.diagonalPolegadas, args.proporcaoTela)
  const cmPorPx = alturaCm / Math.max(1, args.alturaJanelaPx)
  const cmPermitido = Math.tan(alvo * Math.PI / 180) * Math.max(1, args.distanciaCm)
  const pxPermitido = cmPermitido / Math.max(1e-6, cmPorPx)
  const fracao = pxPermitido / Math.max(1, args.alturaJanelaPx)
  // sempre ABAIXO da lente: o texto acima da borda de cima não existe
  return Math.max(0.02, Math.min(0.9, args.lenteY + fracao))
}

/**
 * Reconhece a câmera virtual de um corretor de olhar.
 *
 * NVIDIA Broadcast, OBS e companhia expõem uma câmera virtual; escolhida na
 * lista, a imagem já chega corrigida aqui — sem o editor precisar de modelo
 * nenhum. Vale dizer isso na tela: quem tem a placa quase sempre não sabe que
 * o recurso está ali.
 */
export function ehCameraVirtual(rotulo: string): boolean {
  const r = (rotulo || '').toLowerCase()
  return ['nvidia broadcast', 'obs virtual', 'obs-camera', 'camo', 'xsplit',
          'virtual camera', 'câmera virtual', 'camera virtual', 'e2esoft',
          'droidcam', 'iriun', 'epoccam', 'manycam']
    .some((n) => r.includes(n))
}
