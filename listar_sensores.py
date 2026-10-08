import json
import urllib.request

dados = json.load(urllib.request.urlopen("http://localhost:8085/data.json", timeout=3))


def andar(no, caminho=""):
    nome = no.get("Text", "")
    for filho in no.get("Children", []):
        yield from andar(filho, caminho + "/" + nome)
    if "\u00b0C" in no.get("Value", ""):
        yield caminho + "/" + nome, no["Value"]


for c, v in andar(dados):
    print(v, c)