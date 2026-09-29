"""Revisa el canal de opusdei.org/es-cl y guarda los mensajes nuevos del Prelado.

Corre una vez al día en GitHub Actions. Por cada mensaje nuevo:
  1. lo agrega a mensajes.json (la página index.html lo lee de ahí);
  2. avisa por Telegram con el título y el enlace.

Es idempotente: un mensaje ya avisado no se vuelve a avisar, y si Telegram falla
queda con avisado=false para reintentarlo en la corrida siguiente.

Uso:
    python revisar.py              # corrida normal (pide TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID)
    python revisar.py --sin-aviso  # agrega los nuevos sin mandar nada a Telegram
    python revisar.py --autotest   # prueba el filtro y el parseo, sin red
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

CANAL = "https://opusdei.org/es-cl/lastarticles.xml"
PAGINA = "https://talcaldeg.github.io/lecturas/"
ARCHIVO = Path(__file__).with_name("mensajes.json")
ATOM = {"a": "http://www.w3.org/2005/Atom"}
CATEGORIA = "Cartas pastorales y mensajes"
MESES = {m: i for i, m in enumerate(
    "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split(), 1)}


def es_del_prelado(titulo, categorias):
    """El canal trae todo el sitio; se queda con los mensajes y cartas del Prelado."""
    return CATEGORIA in categorias or "prelado" in titulo.lower()


def fecha_del_titulo(titulo, publicado):
    """'Mensaje del Prelado (25 septiembre 2026)' -> '2026-09-25'; si no trae fecha, la de publicación."""
    m = re.search(r"(\d{1,2})\s+([a-záéíóú]+)\s+(\d{4})", titulo.lower())
    if m and m.group(2) in MESES:
        return f"{m.group(3)}-{MESES[m.group(2)]:02d}-{int(m.group(1)):02d}"
    return publicado[:10]


def leer_canal(xml_texto):
    raiz = ET.fromstring(xml_texto)
    salida = []
    for e in raiz.findall("a:entry", ATOM):
        titulo = (e.findtext("a:title", "", ATOM) or "").strip()
        cats = [c.get("term", "") for c in e.findall("a:category", ATOM)]
        if not es_del_prelado(titulo, cats):
            continue
        link = e.find("a:link[@rel='alternate']", ATOM)
        if link is None:
            link = e.find("a:link", ATOM)
        publicado = e.findtext("a:published", "", ATOM) or e.findtext("a:updated", "", ATOM)
        resumen = re.sub(r"<[^>]+>", "", e.findtext("a:summary", "", ATOM) or "").strip()
        salida.append({
            "url": link.get("href"),
            "titulo": titulo,
            "fecha": fecha_del_titulo(titulo, publicado),
            "publicado": publicado[:10],
            "resumen": resumen[:400],
        })
    return salida


def bajar(url, intentos=4):
    ultimo = None
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (lecturas; lector RSS)"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8")
        except Exception as ex:  # red, 403 de Cloudflare, timeout
            ultimo = ex
            print(f"Intento {i + 1} falló: {ex}")
            time.sleep(10 * (i + 1))
    raise SystemExit(f"ERROR: no se pudo leer {url}: {ultimo}")


def avisar(m, texto=None):
    """Manda el aviso al grupo; cronica.py lo reutiliza con su propio texto."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        raise SystemExit("ERROR: faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID.")
    texto = texto or f"📜 Nuevo: {m['titulo']}\n{m['url']}\n\nPendientes y leídos: {PAGINA}"
    datos = urllib.parse.urlencode({"chat_id": chat, "text": texto}).encode()
    for i in range(3):
        try:
            with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", datos, timeout=30) as r:
                if json.load(r).get("ok"):
                    return True
        except Exception as ex:
            print(f"Telegram, intento {i + 1} falló: {ex}")
            time.sleep(5 * (i + 1))
    return False


def main(argv):
    if "--autotest" in argv:
        return autotest()
    guardados = json.loads(ARCHIVO.read_text(encoding="utf-8")) if ARCHIVO.exists() else []
    por_url = {m["url"]: m for m in guardados}
    nuevos = 0
    for m in leer_canal(bajar(CANAL)):
        if m["url"] not in por_url:
            m["avisado"] = False
            guardados.append(m)
            por_url[m["url"]] = m
            nuevos += 1
            print(f"Nuevo: {m['titulo']}")
    fallas = 0
    if "--sin-aviso" not in argv:
        for m in guardados:
            if not m.get("avisado"):
                m["avisado"] = avisar(m)
                fallas += not m["avisado"]
    guardados.sort(key=lambda m: m["fecha"], reverse=True)
    ARCHIVO.write_text(json.dumps(guardados, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{nuevos} nuevo(s), {len(guardados)} en total.")
    if fallas:
        raise SystemExit(f"ERROR: {fallas} aviso(s) de Telegram sin enviar; se reintentan mañana.")


def autotest():
    xml = """<feed xmlns="http://www.w3.org/2005/Atom">
    <entry><title>Mensaje del Prelado (25 septiembre 2026)</title>
      <link rel="alternate" href="https://x/a/"/><published>2026-09-23T07:24:28</published>
      <category term="Cartas pastorales y mensajes"/><summary>&lt;p&gt;Hola&lt;/p&gt;</summary></entry>
    <entry><title>El Papa desde Francia</title><link href="https://x/b/"/>
      <updated>2026-09-26T10:00:00</updated><category term="De la Iglesia y del Papa"/></entry>
    <entry><title>Carta pastoral del Prelado</title><link href="https://x/c/"/>
      <updated>2026-10-02T10:00:00</updated></entry>
    </feed>"""
    r = leer_canal(xml)
    assert [m["url"] for m in r] == ["https://x/a/", "https://x/c/"], r
    assert r[0]["fecha"] == "2026-09-25" and r[0]["resumen"] == "Hola", r[0]
    assert r[1]["fecha"] == "2026-10-02", r[1]
    print("autotest OK")


if __name__ == "__main__":
    main(sys.argv[1:])
