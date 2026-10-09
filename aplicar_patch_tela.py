"""Liga o diagnóstico de quadros lentos no tela_completa.py.

Adiciona ao Painel um método contexto() e o conecta ao pipeline, para que o log (tela.log)
diga, a cada quadro mais lento que o orçamento, o que estava acontecendo na tela (HUD de
volume, troca de capa, playlist...).

Uso (na pasta do projeto, com o script parado):
    python aplicar_patch_tela.py            # usa ./tela_completa.py
    python aplicar_patch_tela.py caminho\\tela_completa.py

Antes de mudar qualquer coisa, confere se os trechos esperados existem. Se algum não for
encontrado, nada é alterado. Guarda uma cópia em tela_completa.py.bak_patch.
"""
import pathlib
import shutil
import sys

CONTEXTO = '''    def contexto(self):
        """Resumo do estado do painel, usado só nas linhas de log de quadros lentos."""
        return (
            f"hud={self.hud > 0.01} transicao_capa={self.trans is not None} "
            f"playlist={self.exibindo_playlist} texto_animando={self.t_texto is not None} "
            f"p={self.p:.2f}"
        )

'''


def so_uma_vez(src, trecho, nome):
    n = src.count(trecho)
    if n != 1:
        sys.exit(f"[ERRO] trecho '{nome}' encontrado {n} vez(es) (esperado: 1). "
                 "Nada foi alterado.")


def regiao(src, inicio, fim, nome, incluir_fim=True):
    """Devolve (indice_inicial, indice_final) da região entre dois marcadores."""
    so_uma_vez(src, inicio, nome + " (início)")
    i = src.index(inicio)
    # recua até o começo da linha do marcador inicial
    i = src.rfind("\n", 0, i) + 1
    j = src.find(fim, i)
    if j < 0:
        sys.exit(f"[ERRO] fim da região '{nome}' não encontrado. Nada foi alterado.")
    return i, (j + len(fim) if incluir_fim else j)


def main():
    caminho = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "tela_completa.py")
    if not caminho.exists():
        sys.exit(f"[ERRO] {caminho} não encontrado. Rode na pasta do projeto.")
    bruto = caminho.read_bytes().decode("utf-8")
    crlf = "\r\n" in bruto
    src = bruto.replace("\r\n", "\n")

    if "pipe.contexto" in src:
        sys.exit("O patch já parece aplicado (achei pipe.contexto). Nada a fazer.")

    # contexto para o log de quadros lentos
    so_uma_vez(src, "    def quadro(self, agora):", "Painel.quadro")
    src = src.replace("    def quadro(self, agora):", CONTEXTO + "    def quadro(self, agora):", 1)
    so_uma_vez(src, "painel.pipe = pipe", "painel.pipe = pipe")
    src = src.replace("painel.pipe = pipe",
                      "painel.pipe = pipe\n        pipe.contexto = painel.contexto", 1)

    shutil.copyfile(caminho, str(caminho) + ".bak_patch")
    saida = src.replace("\n", "\r\n") if crlf else src
    caminho.write_bytes(saida.encode("utf-8"))
    print(f"OK: {caminho} atualizado (cópia em {caminho}.bak_patch)")


if __name__ == "__main__":
    main()
