import os
import sys
import tkinter as tk

# Descobre o caminho absoluto onde o .exe ou script está guardado
if getattr(sys, 'frozen', False):
    PASTA_ATUAL = os.path.dirname(os.path.abspath(sys.executable))
else:
    PASTA_ATUAL = os.path.dirname(os.path.abspath(__file__))

ARQUIVO_CONFIG_BRILHO = os.path.join(PASTA_ATUAL, "brilho_manual.txt")
ARQUIVO_CONFIG_MODO = os.path.join(PASTA_ATUAL, "modo.txt")

def atualizar_brilho(val):
    try:
        with open(ARQUIVO_CONFIG_BRILHO, "w", encoding="utf-8") as f:
            f.write(str(val))
    except Exception as e:
        print(f"Erro ao gravar brilho: {e}")

def alterar_modo():
    modo_selecionado = var_modo.get()
    try:
        with open(ARQUIVO_CONFIG_MODO, "w", encoding="utf-8") as f:
            f.write(modo_selecionado)
    except Exception as e:
        print(f"Erro ao gravar modo: {e}")

# Lê o modo atual se já existir
modo_inicial = "spotify"
if os.path.exists(ARQUIVO_CONFIG_MODO):
    try:
        with open(ARQUIVO_CONFIG_MODO, "r", encoding="utf-8") as f:
            modo_inicial = f.read().strip()
    except Exception:
        pass

root = tk.Tk()
root.title("Painel de Controlo - Turing Screen")
root.geometry("340x300")
root.resizable(False, False)

# --- SECÇÃO DE MODOS DE VISOR ---
tk.Label(root, text="Selecionar Visor / Modo", font=("Segoe UI", 11, "bold")).pack(pady=10)

var_modo = tk.StringVar(value=modo_inicial)

modos = [
    ("Spotify & Sensores (PC)", "spotify"),
    ("ETS2 (Telemetria Camião)", "ets2"),
    ("Apenas Vídeo de Fundo", "video") # <--- Alterado aqui
]

for texto, valor in modos:
    tk.Radiobutton(
        root, text=texto, variable=var_modo, value=valor, 
        command=alterar_modo, font=("Segoe UI", 9)
    ).pack(anchor="w", padx=40)

# --- SECÇÃO DE BRILHO ---
tk.Label(root, text="Brilho Manual", font=("Segoe UI", 11, "bold")).pack(pady=(15, 5))

slider = tk.Scale(
    root, from_=0, to=100, orient=tk.HORIZONTAL, 
    length=260, command=atualizar_brilho, font=("Segoe UI", 9)
)
slider.set(60)
slider.pack(pady=5)

root.mainloop()