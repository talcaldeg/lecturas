"""Arma un EPUB anual de Crónica (cronicadigital.org) con las secciones elegidas, para leerlo como libro.

Un solo libro por año ("Crónica 2026"), con un capítulo por número mensual publicado y, dentro,
solo las secciones de SECCIONES (él eligió Editorial y Junto al Padre). Cada vez que sale un
número se vuelve a armar el libro completo con el mismo nombre e identificador, así el número
nuevo queda al final y lo ya leído no se mueve.

Inicia sesión con la cuenta del suscriptor, igual que el navegador, y lee las páginas normales
de los artículos. El EPUB es de lectura personal: nunca se guarda en este repo (es público) ni
se imprime su contenido en la consola, porque los registros de GitHub Actions también son
públicos.

Credencial, en este orden:
  1. variables CRONICA_USER y CRONICA_PASS (GitHub Actions);
  2. el archivo DPAPI de Polonia\\apostolado\\cronica\\cronica.cred (este PC).

Uso:
    python cronica_epub.py 2026                  # deja Cronica_2026.epub en Documentos\\Crónica
    python cronica_epub.py 2026 --salida <dir>
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
from datetime import date, datetime, timezone
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
# Sección de cada artículo = campo "location" de su página; se compara por el comienzo.
SECCIONES = ("editorial", "junto al padre")
BLOQUES = {"p", "h1", "h2", "h3", "h4", "h5", "blockquote", "ul", "ol", "figure", "img"}
EXT = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp"}

CSS = """
body { font-family: serif; line-height: 1.5; margin: 0 4%; }
h1 { font-size: 1.5em; margin: 0.6em 0 0.2em; line-height: 1.2; }
h2 { font-size: 1.15em; margin: 1.4em 0 0.4em; }
p { margin: 0 0 0.7em; text-align: justify; }
.seccion { font-variant: small-caps; letter-spacing: 0.05em; color: #666; margin: 0; }
.bajada { font-style: italic; font-size: 1.05em; margin: 0.4em 0 1.2em; }
.cabecera { width: 100%; margin: 0 0 0.6em; }
figure { margin: 1em 0; text-align: center; }
figure img { max-width: 100%; }
figcaption { font-size: 0.85em; color: #666; font-style: italic; }
.portada { text-align: center; margin-top: 30%; }
.portada h1 { font-size: 2.2em; }
.mes { text-align: center; margin-top: 35%; }
.mes h1 { font-size: 2em; }
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


def _inline(el) -> str:
    """Texto con cursivas y negritas, escapado."""
    salida = []
    for nodo in el.descendants:
        if isinstance(nodo, str):
            if nodo.find_parent("figure") is not None and el.name != "figcaption" \
                    and el.find_parent("figure") is None:
                continue
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


def _src(img) -> str | None:
    for attr in ("data-src", "data-lazy-src", "src"):
        v = img.get(attr)
        if v and not v.startswith("data:"):
            return v
    return None


def _cuerpo(cuerpo, urls: list[str]) -> str:
    """XHTML del cuerpo: párrafos, subtítulos, énfasis, citas, listas y fotos con su pie.
    Cada foto queda como src="IMG<n>", con n el índice en urls; se resuelve al armar el EPUB."""
    def foto(img, pie: str = "") -> str:
        u = _src(img)
        if not u:
            return ""
        urls.append(u)
        cap = f"<figcaption>{pie}</figcaption>" if pie else ""
        return f'<figure><img src="IMG{len(urls) - 1}" alt=""/>{cap}</figure>'

    partes = []
    for el in cuerpo.find_all(BLOQUES):
        if any(p.name in BLOQUES for p in el.parents if p is not cuerpo and p.name):
            continue  # ya lo cubre el bloque que lo contiene
        if el.name in ("h1", "h2", "h3", "h4", "h5"):
            t = _texto(el)
            if t:
                partes.append(f"<h2>{html.escape(t)}</h2>")
        elif el.name == "figure":
            img = el.find("img")
            pie = _inline(el.find("figcaption")) if el.find("figcaption") else ""
            if img is not None:
                partes.append(foto(img, pie))
            elif pie:
                partes.append(f"<p>{pie}</p>")
        elif el.name == "img":
            partes.append(foto(el))
        elif el.name in ("ul", "ol"):
            items = "".join(f"<li>{_inline(li)}</li>" for li in el.find_all("li", recursive=False))
            partes.append(f"<{el.name}>{items}</{el.name}>")
        elif el.name == "blockquote":
            partes.append(f"<blockquote><p>{_inline(el)}</p></blockquote>")
        else:
            for img in el.find_all("img"):
                partes.append(foto(img))
            t = _inline(el)
            if t.strip():
                partes.append(f"<p>{t}</p>")
    return "\n".join(p for p in partes if p)


def leer_articulo(s: requests.Session, url: str, urls: list[str]) -> dict | None:
    """El artículo, o None si no es de una sección elegida."""
    r = s.get(url, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    if soup.select_one(".um-page-loggedout"):
        sys.exit("Un artículo volvió al login: la sesión no quedó iniciada.")
    seccion = _texto(soup.select_one(".post_custom_field.location"))
    if not seccion.lower().startswith(SECCIONES):
        return None
    cuerpo = soup.select_one(".post_content")
    if cuerpo is None:
        sys.exit("Cambió la estructura del sitio: no aparece .post_content.")
    titulo = _texto(soup.select_one(".post_title")) or seccion
    cabecera = None
    img = soup.select_one(".post_image img")
    if img is not None and _src(img):
        urls.append(_src(img))
        cabecera = len(urls) - 1
    return {
        "titulo": titulo,
        "seccion": seccion,
        "bajada": _texto(soup.select_one(".post_custom_field.foreword")),
        "cabecera": cabecera,
        "cuerpo": _cuerpo(cuerpo, urls),
    }


def bajar_imagen(s: requests.Session, url: str) -> tuple[bytes, str] | None:
    try:
        r = s.get(url, timeout=30)
        r.raise_for_status()
    except requests.RequestException:
        return None
    tipo = r.headers.get("Content-Type", "").split(";")[0] or mimetypes.guess_type(url)[0] or ""
    return (r.content, tipo) if tipo in EXT else None


# ---------- EPUB ----------

def _xhtml(titulo: str, cuerpo: str) -> str:
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
            'xml:lang="es" lang="es">\n<head><meta charset="utf-8"/>'
            f'<title>{html.escape(titulo)}</title>'
            '<link rel="stylesheet" type="text/css" href="estilo.css"/></head>\n'
            f'<body>\n{cuerpo}\n</body>\n</html>\n')


def armar_epub(destino: Path, anio: int, numeros: list[tuple[str, list[dict]]],
               imagenes: dict[int, tuple[bytes, str]]) -> None:
    """numeros: [(nombre del mes, artículos)] en orden. Escribe a un temporal y reemplaza."""
    titulo_libro = f"Crónica {anio}"
    ident = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, f'cronica {anio} editorial y junto al padre')}"
    manifest, spine, nav = [], [], []
    D = zipfile.ZIP_DEFLATED

    def img_tag(m: re.Match) -> str:
        return f'src="img{int(m.group(1)):03d}.{EXT[imagenes[int(m.group(1))][1]]}"'

    def resolver(xhtml: str) -> str:
        # Fotos que no se pudieron bajar: se quita la figura entera.
        xhtml = re.sub(r'<figure><img src="IMG(\d+)" alt=""/>.*?</figure>',
                       lambda m: m.group(0) if int(m.group(1)) in imagenes else "", xhtml)
        xhtml = re.sub(r'<img class="cabecera" src="IMG(\d+)" alt=""/>',
                       lambda m: m.group(0) if int(m.group(1)) in imagenes else "", xhtml)
        return re.sub(r'src="IMG(\d+)"', img_tag, xhtml)

    tmp = destino.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?>\n<container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                   '</rootfiles></container>', compress_type=D)
        z.writestr("OEBPS/estilo.css", CSS, compress_type=D)
        manifest.append('<item id="css" href="estilo.css" media-type="text/css"/>')

        portada = (f'<div class="portada"><h1>{titulo_libro}</h1>'
                   '<p>Editorial y Junto al Padre</p></div>')
        z.writestr("OEBPS/portada.xhtml", _xhtml(titulo_libro, portada), compress_type=D)
        manifest.append('<item id="portada" href="portada.xhtml" media-type="application/xhtml+xml"/>')
        spine.append('<itemref idref="portada"/>')

        for n, (mes, arts) in enumerate(numeros, 1):
            mid = f"m{n:02d}"
            z.writestr(f"OEBPS/{mid}.xhtml", _xhtml(mes, f'<div class="mes"><h1>{html.escape(mes)}</h1></div>'),
                       compress_type=D)
            manifest.append(f'<item id="{mid}" href="{mid}.xhtml" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="{mid}"/>')
            sub = []
            for i, a in enumerate(arts, 1):
                partes = []
                if a["cabecera"] is not None:
                    partes.append(f'<img class="cabecera" src="IMG{a["cabecera"]}" alt=""/>')
                partes.append(f'<p class="seccion">{html.escape(a["seccion"])}</p>')
                partes.append(f'<h1>{html.escape(a["titulo"])}</h1>')
                if a["bajada"]:
                    partes.append(f'<p class="bajada">{html.escape(a["bajada"])}</p>')
                partes.append(a["cuerpo"])
                aid = f"{mid}a{i:02d}"
                z.writestr(f"OEBPS/{aid}.xhtml", resolver(_xhtml(a["titulo"], "\n".join(partes))),
                           compress_type=D)
                manifest.append(f'<item id="{aid}" href="{aid}.xhtml" media-type="application/xhtml+xml"/>')
                spine.append(f'<itemref idref="{aid}"/>')
                sub.append(f'<li><a href="{aid}.xhtml">{html.escape(a["seccion"])}: '
                           f'{html.escape(a["titulo"])}</a></li>')
            nav.append(f'<li><a href="{mid}.xhtml">{html.escape(mes)}</a><ol>{"".join(sub)}</ol></li>')

        for k, (datos, tipo) in sorted(imagenes.items()):
            archivo = f"img{k:03d}.{EXT[tipo]}"
            z.writestr(f"OEBPS/{archivo}", datos, compress_type=zipfile.ZIP_STORED)
            manifest.append(f'<item id="img{k:03d}" href="{archivo}" media-type="{tipo}"/>')

        indice = '<nav epub:type="toc" id="toc"><h1>Índice</h1><ol>\n' + "\n".join(nav) + '\n</ol></nav>'
        z.writestr("OEBPS/nav.xhtml", _xhtml("Índice", indice), compress_type=D)
        manifest.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
        spine.insert(1, '<itemref idref="nav"/>')

        modificado = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        opf = ('<?xml version="1.0" encoding="utf-8"?>\n'
               '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid" xml:lang="es">\n'
               '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
               f'<dc:identifier id="uid">{ident}</dc:identifier>\n'
               f'<dc:title>{titulo_libro}</dc:title>\n'
               '<dc:language>es</dc:language>\n<dc:publisher>Crónica</dc:publisher>\n'
               f'<meta property="dcterms:modified">{modificado}</meta>\n'
               '</metadata>\n<manifest>\n' + "\n".join(manifest) + '\n</manifest>\n'
               '<spine>\n' + "\n".join(spine) + '\n</spine>\n</package>\n')
        z.writestr("OEBPS/content.opf", opf, compress_type=D)
    tmp.replace(destino)


def generar(anio: int, salida: Path) -> tuple[Path, list[str]]:
    """Arma el libro del año con todos los números publicados. Devuelve la ruta y los meses."""
    s = sesion()
    hoy = date.today()
    ultimo = 12 if anio < hoy.year else hoy.month
    urls: list[str] = []
    numeros: list[tuple[str, list[dict]]] = []
    for mes in range(1, ultimo + 1):
        arts = [a for a in (leer_articulo(s, u, urls) for u in articulos(s, mes, anio)) if a]
        if arts:
            numeros.append((f"{MESES[mes - 1].capitalize()} {anio}", arts))
    if not numeros:
        sys.exit(f"Crónica {anio}: sin números publicados.")
    imagenes = {}
    for k, u in enumerate(urls):
        img = bajar_imagen(s, u)
        if img:
            imagenes[k] = img
    salida.mkdir(parents=True, exist_ok=True)
    destino = salida / f"Cronica_{anio}.epub"
    armar_epub(destino, anio, numeros, imagenes)
    # Solo conteos: los registros de Actions son públicos.
    total = sum(len(a) for _, a in numeros)
    print(f"Crónica {anio}: {len(numeros)} números, {total} artículos, "
          f"{len(imagenes)}/{len(urls)} imágenes -> {destino.name}")
    return destino, [m for m, _ in numeros]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("anio", type=int, help="2026")
    ap.add_argument("--salida", type=Path, default=SALIDA)
    a = ap.parse_args()
    generar(a.anio, a.salida)


if __name__ == "__main__":
    main()
