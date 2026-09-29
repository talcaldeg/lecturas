"""Revisa qué números mensuales de Crónica (cronicadigital.org) salieron y avisa los nuevos.

Corre una vez al día en GitHub Actions, después de revisar.py. No inicia sesión: la API
pública de WordPress (/wp-json/wp/v2/categories) lista cada número como una categoría
"<Mes> <año>" con su conteo de artículos. Los meses se crean por adelantado con 0 artículos,
así que un número "salió" cuando su conteo pasa de 0.

Solo se guarda el mes, el año y el enlace al número: el contenido de la revista es
restringido y la página es pública, así que nunca se leen títulos ni textos de artículos.

Por cada número nuevo:
  1. lo agrega a cronica.json (la página cronica.html lo lee de ahí);
  2. avisa por Telegram al mismo grupo "Lecturas".

Es idempotente: un número ya avisado no se vuelve a avisar, y si Telegram falla queda con
avisado=false para reintentarlo en la corrida siguiente.

Uso:
    python cronica.py                     # corrida normal (pide TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID)
    python cronica.py --marcar-avisados   # carga los que ya salieron sin avisar (carga inicial)
    python cronica.py --autotest          # prueba el filtro, sin red
"""

import json
import sys
import time
import urllib.request
from pathlib import Path

import revisar  # se reutilizan MESES y el envío a Telegram

API = "https://cronicadigital.org/wp-json/wp/v2/categories?per_page=100&_fields=id,count,name,parent,link&page={}"
PAGINA = "https://talcaldeg.github.io/lecturas/cronica.html"
ARCHIVO = Path(__file__).with_name("cronica.json")
DESDE = "2026-01"  # él eligió listar desde enero de 2026
MESES = revisar.MESES


def numeros_publicados(categorias):
    """De todas las categorías, los números en español con al menos un artículo.

    Un número es una categoría '<Mes> <año>' bajo /es/category/ cuyo padre es la categoría
    del año. Hay categorías con nombre en español colgadas de otros idiomas (/pt/, /fr/):
    se descartan por el enlace.
    """
    anos = {c["id"] for c in categorias if "/es/category/" in c["link"] and c["name"].strip().isdigit()}
    salida = []
    for c in categorias:
        if "/es/category/" not in c["link"] or c["parent"] not in anos or c["count"] < 1:
            continue
        partes = c["name"].strip().lower().split()
        if len(partes) != 2 or partes[0] not in MESES or not partes[1].isdigit():
            continue
        fecha = f"{partes[1]}-{MESES[partes[0]]:02d}"
        if fecha < DESDE:
            continue
        salida.append({"id": c["id"], "fecha": fecha, "nombre": f"Crónica {partes[0]} {partes[1]}",
                       "url": c["link"]})
    return salida


def bajar_categorias():
    todas, pagina, total = [], 1, 1
    while pagina <= total:
        for i in range(4):
            try:
                req = urllib.request.Request(API.format(pagina), headers={"User-Agent": "Mozilla/5.0 (lecturas)"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    total = int(r.headers.get("X-WP-TotalPages", "1"))
                    todas += json.load(r)
                break
            except Exception as ex:
                print(f"Página {pagina}, intento {i + 1} falló: {ex}")
                time.sleep(10 * (i + 1))
        else:
            raise SystemExit(f"ERROR: no se pudo leer la página {pagina} de categorías de Crónica.")
        pagina += 1
    return todas


def avisar(n):
    return revisar.avisar(n, f"📰 Salió {n['nombre']}\n{n['url']}\n\nPendientes y leídos: {PAGINA}")


def main(argv):
    if "--autotest" in argv:
        return autotest()
    guardados = json.loads(ARCHIVO.read_text(encoding="utf-8")) if ARCHIVO.exists() else []
    por_id = {n["id"]: n for n in guardados}
    nuevos = 0
    for n in numeros_publicados(bajar_categorias()):
        if n["id"] not in por_id:
            n["avisado"] = "--marcar-avisados" in argv
            guardados.append(n)
            por_id[n["id"]] = n
            nuevos += 1
            print(f"Nuevo: {n['nombre']}")
    fallas = 0
    for n in guardados:
        if not n.get("avisado"):
            n["avisado"] = avisar(n)
            fallas += not n["avisado"]
    guardados.sort(key=lambda n: n["fecha"], reverse=True)
    ARCHIVO.write_text(json.dumps(guardados, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{nuevos} nuevo(s), {len(guardados)} en total.")
    if fallas:
        raise SystemExit(f"ERROR: {fallas} aviso(s) de Telegram sin enviar; se reintentan mañana.")


def autotest():
    es = "https://cronicadigital.org/es/category/"
    cats = [
        {"id": 1195, "count": 0, "name": "2026", "parent": 0, "link": es + "2026-es/"},
        {"id": 872, "count": 0, "name": "2025", "parent": 0, "link": es + "2025-es/"},
        {"id": 1247, "count": 9, "name": "Agosto 2026", "parent": 1195, "link": es + "2026-es/agosto-2026-es/"},
        {"id": 1249, "count": 0, "name": "Septiembre 2026", "parent": 1195, "link": es + "2026-es/septiembre-2026-es/"},
        {"id": 874, "count": 8, "name": "Diciembre 2025", "parent": 872, "link": es + "2025-es/diciembre-2025-es/"},
        {"id": 1353, "count": 3, "name": "Marzo 2026", "parent": 1195,
         "link": "https://cronicadigital.org/pt/category/2026-es/marzo-2026-es/"},
        {"id": 1, "count": 21, "name": "Uncategorized", "parent": 0, "link": es + "uncategorized/"},
        {"id": 1261, "count": 10, "name": "May 2026", "parent": 1206,
         "link": "https://cronicadigital.org/en/category/2026-en/may-2026-en/"},
    ]
    r = numeros_publicados(cats)
    assert r == [{"id": 1247, "fecha": "2026-08", "nombre": "Crónica agosto 2026",
                  "url": es + "2026-es/agosto-2026-es/"}], r
    print("autotest OK")


if __name__ == "__main__":
    main(sys.argv[1:])
