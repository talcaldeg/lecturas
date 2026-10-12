"""Arma un EPUB con un número mensual de Crónica (cronicadigital.org), para leerlo como libro.

Inicia sesión con la cuenta del suscriptor, igual que el navegador, y lee las páginas normales
de los artículos. El EPUB es de lectura personal: nunca se guarda en este repo (es público) ni
se imprime su contenido en la consola, porque los registros de GitHub Actions también son
públicos.

Credencial, en este orden:
  1. variables CRONICA_USER y CRONICA_PASS (GitHub Actions);
  2. el archivo DPAPI de Polonia\\apostolado\\cronica\\cronica.cred (este PC).

Uso:
    python cronica_epub.py "Agosto 2026"                 # deja Cronica_2026-08.epub en Documentos\\Crónica
    python cronica_epub.py "Agosto 2026" --salida <dir>
"""
from __future__ import annotations

import argparse
import html
import mimetypes
import os
import re
import subprocess
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = "https://cronicadigital.org"
LOGIN_URL = f"{BASE}/es/"
CRED_DPAPI = (Path.home() / "Desktop" / "Personal" / "Claude" / "Polonia" / "apostolado"
              / "cronica" / "cronica.cred")
SALIDA = Path.home() / "Documents" / "Crónica"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) lectura personal"
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]

CSS = """
body { font-family: serif; line-height: 1.5; margin: 0 4%; }
h1 { font-size: 1.5em; margin: 0.6em 0 0.2em; line-height: 1.2; }
h2 { font-size: 1.15em; margin: 1.4em 0 0.4em; }
p { margin: 0 0 0.7em; text-align: justify; }
.lugar { font-variant: small-caps; letter-spacing: 0.05em; color: #666; margin: 0; }
.bajada { font-style: italic; font-size: 1.05em; margin: 0.4em 0 1.2em; }
.cabecera { width: 100%; margin: 0 0 0.6em; }
.portada { text-align: center; margin-top: 30%; }
.portada h1 { font-size: 2.2em; }
"""


# ---------- sesión ----------

def _credencial() -> tuple[str, str]:
    usuario, clave = os.environ.get("CRONICA_USER"), os.environ.get("CRONICA_PASS")
    if usuario and clave:
        return usuario, clave
    if not CRED_DPAPI.exists():
        sys.exit("Sin credencial: faltan CRONICA_USER/CRONICA_PASS y no existe " + str(CRED_DPAPI))
    ps = (f"$c = Import-Clixml '{CRED_DPAPI}'; "
          "$c.UserName + [char]0 + $c.GetNetworkCredential().Password")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                         capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0 or "\0" not in out.stdout:
        sys.exit("No se pudo descifrar cronica.cred; regenerarla con Save-CronicaCred.ps1")
    usuario, clave = out.stdout.rstrip("\r\n").split("\0", 1)
    return usuario, clave


def sesion() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    reintentos = Retry(total=5, backoff_factor=2, allowed_methods=["GET"],
                       status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=reintentos))
    pagina = s.get(LOGIN_URL, timeout=30)
    pagina.raise_for_status()
    soup = BeautifulSoup(pagina.text, "html.parser")
    form = soup.find("input", {"name": "form_id"}).find_parent("form")
    datos = {i["name"]: i.get("value", "") for i in form.find_all("input")
             if i.get("name") and i.get("type") not in ("checkbox", "submit")}
    form_id = datos["form_id"]
    usuario, clave = _credencial()
    datos[f"username-{form_id}"] = usuario
    datos[f"user_password-{form_id}"] = clave
    datos["rememberme"] = "1"
    s.post(LOGIN_URL, data=datos, timeout=30).raise_for_status()
    if not any(c.name.startswith("wordpress_logged_in") for c in s.cookies):
        sys.exit("El sitio no aceptó el inicio de sesión (revisar usuario y clave).")
    return s


# ---------- lectura ----------

def _mes_anio(nombre: str) -> tuple[int, int]:
    mes, anio = nombre.strip().lower().split()
    if mes not in MESES:
        sys.exit(f"Mes no reconocido: {mes}")
    return MESES.index(mes) + 1, int(anio)


def articulos(s: requests.Session, mes: int, anio: int) -> list[str]:
    """Enlaces de los artículos del número, en el orden en que los muestra el sitio."""
    base = f"{BASE}/es/category/{anio}-es/{MESES[mes - 1]}-{anio}-es/"
    prefijo = f"{BASE}/es/{anio}-es/{MESES[mes - 1]}-{anio}-es/"
    enlaces: list[str] = []
    pag = 1
    while True:
        r = s.get(base if pag == 1 else f"{base}page/{pag}/", timeout=30)
        if r.status_code != 200:
            break
        nuevos = 0
        for a in BeautifulSoup(r.text, "html.parser").select("a[href]"):
            href = a["href"].split("#")[0]
            if href.startswith(prefijo) and href != prefijo and href not in enlaces:
                enlaces.append(href)
                nuevos += 1
        if not nuevos:
            break
        pag += 1
    return enlaces


def _texto(el) -> str:
    return el.get_text(" ", strip=True) if el else ""


def _limpiar_cuerpo(cuerpo) -> str:
    """Deja solo p, subtítulos, énfasis, citas y listas, como XHTML válido."""
    partes = []
    for el in cuerpo.find_all(["p", "h1", "h2", "h3", "h4", "blockquote", "ul", "ol"], recursive=True):
        if el.find_parent(["blockquote", "ul", "ol"]) not in (None,) and el.name not in ("blockquote", "ul", "ol"):
            continue
        if el.name in ("h1", "h2", "h3", "h4"):
            t = _texto(el)
            if t:
                partes.append(f"<h2>{html.escape(t)}</h2>")
        elif el.name in ("ul", "ol"):
            items = "".join(f"<li>{_inline(li)}</li>" for li in el.find_all("li", recursive=False))
            partes.append(f"<{el.name}>{items}</{el.name}>")
        elif el.name == "blockquote":
            partes.append(f"<blockquote><p>{_inline(el)}</p></blockquote>")
        else:
            t = _inline(el)
            if t.strip():
                partes.append(f"<p>{t}</p>")
    return "\n".join(partes)


def _inline(el) -> str:
    """Texto con cursivas y negritas, escapado."""
    salida = []
    for nodo in el.descendants:
        if isinstance(nodo, str):
            padres = {p.name for p in nodo.parents if p is not el and p is not None}
            t = html.escape(str(nodo))
            if padres & {"em", "i"}:
                t = f"<em>{t}</em>"
            if padres & {"strong", "b"}:
                t = f"<strong>{t}</strong>"
            salida.append(t)
        elif getattr(nodo, "name", None) == "br":
            salida.append("<br/>")
    return re.sub(r"\s+", " ", "".join(salida)).strip()


def leer_articulo(s: requests.Session, url: str) -> dict:
    r = s.get(url, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    if soup.select_one(".um-page-loggedout"):
        sys.exit("Un artículo volvió al login: la sesión no quedó iniciada.")
    cuerpo = soup.select_one(".post_content")
    if cuerpo is None:
        sys.exit("Cambió la estructura del sitio: no aparece .post_content.")
    titulo = _texto(soup.select_one(".post_title")) or (soup.find("meta", property="og:title") or {}).get("content", "")
    img = soup.select_one(".post_image img")
    img_url = None
    if img is not None:
        img_url = img.get("data-src") or img.get("src")
    return {
        "titulo": titulo,
        "lugar": _texto(soup.select_one(".post_custom_field.location")),
        "bajada": _texto(soup.select_one(".post_custom_field.foreword")),
        "cuerpo": _limpiar_cuerpo(cuerpo),
        "img_url": img_url,
    }


def bajar_imagen(s: requests.Session, url: str) -> tuple[bytes, str] | None:
    try:
        r = s.get(url, timeout=30)
        r.raise_for_status()
    except requests.RequestException:
        return None
    tipo = r.headers.get("Content-Type", "").split(";")[0] or mimetypes.guess_type(url)[0] or ""
    if tipo not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
        return None
    return r.content, tipo


# ---------- EPUB ----------

def _xhtml(titulo: str, cuerpo: str) -> str:
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
            'xml:lang="es" lang="es">\n<head><meta charset="utf-8"/>'
            f'<title>{html.escape(titulo)}</title>'
            '<link rel="stylesheet" type="text/css" href="estilo.css"/></head>\n'
            f'<body>\n{cuerpo}\n</body>\n</html>\n')


def armar_epub(destino: Path, nombre: str, arts: list[dict], imagenes: dict[int, tuple[bytes, str]]) -> None:
    ident = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, 'cronica ' + nombre)}"
    ext = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp"}
    manifest, spine, nav = [], [], []

    with zipfile.ZipFile(destino, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?>\n<container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                   '</rootfiles></container>', compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/estilo.css", CSS, compress_type=zipfile.ZIP_DEFLATED)
        manifest.append('<item id="css" href="estilo.css" media-type="text/css"/>')

        portada = (f'<div class="portada"><h1>Crónica</h1><p>{html.escape(nombre)}</p></div>')
        z.writestr("OEBPS/portada.xhtml", _xhtml(f"Crónica {nombre}", portada), compress_type=zipfile.ZIP_DEFLATED)
        manifest.append('<item id="portada" href="portada.xhtml" media-type="application/xhtml+xml"/>')
        spine.append('<itemref idref="portada"/>')

        for i, a in enumerate(arts, 1):
            partes = []
            if i in imagenes:
                datos, tipo = imagenes[i]
                archivo = f"img{i:02d}.{ext[tipo]}"
                z.writestr(f"OEBPS/{archivo}", datos, compress_type=zipfile.ZIP_STORED)
                manifest.append(f'<item id="img{i:02d}" href="{archivo}" media-type="{tipo}"/>')
                partes.append(f'<img class="cabecera" src="{archivo}" alt=""/>')
            if a["lugar"]:
                partes.append(f'<p class="lugar">{html.escape(a["lugar"])}</p>')
            partes.append(f'<h1>{html.escape(a["titulo"])}</h1>')
            if a["bajada"]:
                partes.append(f'<p class="bajada">{html.escape(a["bajada"])}</p>')
            partes.append(a["cuerpo"])
            archivo = f"art{i:02d}.xhtml"
            z.writestr(f"OEBPS/{archivo}", _xhtml(a["titulo"], "\n".join(partes)),
                       compress_type=zipfile.ZIP_DEFLATED)
            manifest.append(f'<item id="art{i:02d}" href="{archivo}" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="art{i:02d}"/>')
            nav.append(f'<li><a href="{archivo}">{html.escape(a["titulo"])}</a></li>')

        indice = (f'<nav epub:type="toc" id="toc"><h1>Índice</h1><ol>\n'
                  + "\n".join(nav) + '\n</ol></nav>')
        z.writestr("OEBPS/nav.xhtml", _xhtml("Índice", indice), compress_type=zipfile.ZIP_DEFLATED)
        manifest.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
        spine.insert(1, '<itemref idref="nav"/>')

        modificado = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        opf = ('<?xml version="1.0" encoding="utf-8"?>\n'
               '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid" xml:lang="es">\n'
               '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
               f'<dc:identifier id="uid">{ident}</dc:identifier>\n'
               f'<dc:title>Crónica {html.escape(nombre)}</dc:title>\n'
               '<dc:language>es</dc:language>\n<dc:publisher>Crónica</dc:publisher>\n'
               f'<meta property="dcterms:modified">{modificado}</meta>\n'
               '</metadata>\n<manifest>\n' + "\n".join(manifest) + '\n</manifest>\n'
               '<spine>\n' + "\n".join(spine) + '\n</spine>\n</package>\n')
        z.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)


def generar(nombre: str, salida: Path) -> Path:
    mes, anio = _mes_anio(nombre)
    nombre = f"{MESES[mes - 1].capitalize()} {anio}"
    s = sesion()
    enlaces = articulos(s, mes, anio)
    if not enlaces:
        sys.exit(f"Crónica {nombre}: sin artículos (¿no ha salido?).")
    arts, imagenes = [], {}
    for i, url in enumerate(enlaces, 1):
        a = leer_articulo(s, url)
        arts.append(a)
        if a["img_url"]:
            img = bajar_imagen(s, a["img_url"])
            if img:
                imagenes[i] = img
    salida.mkdir(parents=True, exist_ok=True)
    destino = salida / f"Cronica_{anio}-{mes:02d}.epub"
    armar_epub(destino, nombre, arts, imagenes)
    # Solo conteos: los registros de Actions son públicos.
    print(f"Crónica {nombre}: {len(arts)} artículos, {len(imagenes)} imágenes -> {destino.name}")
    return destino


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("numero", help='"Agosto 2026"')
    ap.add_argument("--salida", type=Path, default=SALIDA)
    a = ap.parse_args()
    generar(a.numero, a.salida)


if __name__ == "__main__":
    main()
