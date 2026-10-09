"""Teste da transmissão ao vivo + animação de troca de capa na tela 2,1".

Feche o tela_completa.py e o app oficial antes. Dura uns 16 s e mostra no console
um resumo de desempenho. Não usa Spotify: as capas são desenhadas aqui.

Rode:  python teste_ao_vivo.py
Opções: --delay (usa a espera de controle de fluxo da CLI), --kbps 1500, --lote 160000
"""
import argparse
import sys
import time

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import animacao_capa
import ao_vivo

VIDEO = "video_tela.mp4"
DURACAO_TESTE = 16.0
TROCAS = [3.0, 7.0, 11.0]          # segundos em que a "música" muda
FLASH_EM = 14.0                    # um clarão para medir o atraso da tela
CAPAS = [("A", (230, 70, 60), (120, 20, 90)),
         ("B", (40, 160, 230), (20, 40, 120)),
         ("C", (250, 190, 40), (230, 90, 30)),
         ("D", (60, 200, 120), (10, 90, 80))]


def fonte(tam):
    for nome in ("segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(nome, tam)
        except OSError:
            pass
    try:
        return ImageFont.load_default(tam)
    except TypeError:
        return ImageFont.load_default()


def capa_demo(letra, c1, c2, tam=240):
    grad = Image.linear_gradient("L").resize((tam, tam)).rotate(45)
    img = Image.composite(Image.new("RGB", (tam, tam), c1), Image.new("RGB", (tam, tam), c2), grad)
    ImageDraw.Draw(img).text((tam // 2, tam // 2), letra, font=fonte(130),
                             fill=(255, 255, 255), anchor="mm")
    grande = tam * 4
    mascara = Image.new("L", (grande, grande), 0)
    ImageDraw.Draw(mascara).ellipse((0, 0, grande - 1, grande - 1), fill=255)
    saida = img.convert("RGBA")
    saida.putalpha(mascara.resize((tam, tam), Image.LANCZOS))
    return saida


def compor(bg, titulo, artista, capa, cor, frac, flash):
    """Mesmo desenho do painel real, simplificado: halo, anel, capa e texto."""
    img = bg.convert("RGBA")
    halo = Image.new("RGBA", (120, 120), (0, 0, 0, 0))
    ImageDraw.Draw(halo).ellipse((26, 26, 94, 94), fill=(cor[0], cor[1], cor[2], 150))
    halo = halo.filter(ImageFilter.GaussianBlur(10)).resize((480, 480), Image.BILINEAR)
    img.alpha_composite(halo)

    camada = Image.new("RGBA", (480, 480), (0, 0, 0, 0))   # desenhos com transparência
    d = ImageDraw.Draw(camada)
    r = 138
    d.arc((240 - r, 240 - r, 240 + r, 240 + r), 0, 360, fill=(255, 255, 255, 40), width=4)
    d.arc((240 - r, 240 - r, 240 + r, 240 + r), -90, -90 + int(360 * frac),
          fill=(cor[0], cor[1], cor[2], 255), width=4)
    d.text((240, 398), titulo, font=fonte(32), fill=(255, 255, 255, 255), anchor="mt")
    d.text((240, 433), artista, font=fonte(20), fill=(200, 200, 200, 255), anchor="mt")
    img.alpha_composite(camada)
    if capa is not None:
        img.alpha_composite(capa, (120, 120))
    if flash > 0:
        img.alpha_composite(Image.new("RGBA", (480, 480), (255, 255, 255, int(220 * flash))))
    return img.convert("RGB")


class Cena:
    """Estado da demonstração: qual 'música' toca e se há transição em curso."""

    def __init__(self, fundo):
        self.fundo = fundo
        self.capas = [(l, capa_demo(l, c1, c2), (*c1, 255)) for l, c1, c2 in CAPAS]
        self.gatilhos = [0.5] + TROCAS
        self.prox = 0
        self.indice = -1
        self.trans = None
        self.titulo = ""
        self.t0 = None
        self.flash_ate = 0.0
        self.flash_feito = False

    def trocar(self, agora):
        ant = self.capas[self.indice] if self.indice >= 0 else None
        self.indice = (self.indice + 1) % len(self.capas)
        nova = self.capas[self.indice]
        self.trans = animacao_capa.TransicaoCapa(
            ant[1] if ant else None, ant[2] if ant else None, nova[1], nova[2], inicio=agora)

    def quadro(self, agora):
        if self.t0 is None:
            self.t0 = agora
        dt = agora - self.t0
        if self.prox < len(self.gatilhos) and dt >= self.gatilhos[self.prox]:
            self.prox += 1
            self.trocar(agora)

        capa_img, cor = None, (30, 215, 96, 255)
        if self.trans is not None:
            capa_img, cor, metade, fim = self.trans.quadro(agora)
            if metade:
                self.titulo = f"Faixa {self.capas[self.indice][0]}"
            if fim:
                self.trans = None
        if self.trans is None and self.indice >= 0:
            capa_img, cor = self.capas[self.indice][1], self.capas[self.indice][2]

        flash = 0.0
        if not self.flash_feito and dt >= FLASH_EM:
            self.flash_feito = True
            self.flash_ate = agora + 0.3
            print(f"[{dt:5.1f}s] CLARÃO gerado agora: veja quanto tempo a tela leva para acender")
        if agora < self.flash_ate:
            flash = (self.flash_ate - agora) / 0.3

        frac = (dt % 20) / 20
        bg = self.fundo.proximo() or Image.new("RGB", (480, 480), (10, 10, 20))
        return compor(bg, self.titulo, "Artista de teste", capa_img, cor, frac, flash)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", action="store_true")
    ap.add_argument("--kbps", type=int, default=1500)
    ap.add_argument("--lote", type=int, default=ao_vivo.MAX_LOTE)
    args = ap.parse_args()
    import libusb_package
    from turingscreencli import operations
    from turingscreencli.transport import (
        build_command_packet_header, encrypt_command_packet, write_to_device)

    dev = libusb_package.find(idVendor=0x1CBE, idProduct=0x21)
    if dev is None:
        sys.exit("tela não encontrada (feche o app oficial e o tela_completa.py)")
    dev.set_configuration()

    def cmd(n):
        return write_to_device(dev, encrypt_command_packet(build_command_packet_header(n)))

    for n in (111, 112, 13):
        cmd(n)
    operations.send_brightness_command(dev, 60)
    cmd(41)
    operations.clear_image(dev)
    operations.send_frame_rate_command(dev, 30)

    fundo = ao_vivo.FundoDecoder(VIDEO)
    pipe = ao_vivo.PipelineAoVivo(dev, Cena(fundo).quadro, fps=30, kbps=args.kbps,
                                  usar_delay=args.delay, max_lote=args.lote)
    print(f"transmitindo ao vivo (delay={'sim' if args.delay else 'não'}, {args.kbps} kbps)... Ctrl+C para parar")
    try:
        r = pipe.rodar(duracao=DURACAO_TESTE)
    except KeyboardInterrupt:
        r = None
    finally:
        fundo.fechar()
        cmd(123)
    if r:
        n = max(r["comandos"], 1)
        print("\n=== RESUMO ===")
        print(f"quadros gerados: {r['quadros']} em {r['segundos']:.1f}s = {r['fps_real']:.1f} por segundo "
              f"(gerar cada quadro: {1000 * r['t_gerar']:.0f} ms em média, {1000 * r['t_gerar_max']:.0f} ms no pior)")
        print(f"vezes em que o gerador atrasou mais de 0,25 s: {r['atrasos']}")
        print(f"comandos enviados: {r['comandos']} ({r['bytes'] // 1024} KB, {r['bytes'] // 1024 // n} KB por comando), "
              f"tempo médio por comando: {1000 * r['t_envio'] / n:.0f} ms")
        print(f"atraso do dado (saída do ffmpeg até a tela receber): média {1000 * r['lat_soma'] / n:.0f} ms, "
              f"pior {1000 * r['lat_max']:.0f} ms")
        print(f"resposta da tela (byte 8): mínimo {r['resp_min']}, máximo {r['resp_max']}")
        print(f"esperas de controle de fluxo: {r['delays']} ({r['t_delay']:.1f}s no total), "
              f"falhas de envio: {r['falhas']}")

if __name__ == "__main__":
    main()
