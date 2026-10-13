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
    python cronica_epub.py --enviar-pendientes --salida <dir>   # GitHub Actions
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

# Un título no se parte entre dos páginas ni queda solo al pie de una: va entero y pegado a
# lo que sigue. "break-after: avoid" no lo respetan todos los lectores, así que además el
# título viaja en un bloque .junto con su primer párrafo (ver _juntar).
NO_CORTAR = ("page-break-inside: avoid; break-inside: avoid; "
             "-webkit-column-break-inside: avoid;")
NO_SEPARAR = ("page-break-after: avoid; break-after: avoid; "
              "-webkit-column-break-after: avoid;")
CSS = f"""
body {{ font-family: serif; line-height: 1.5; margin: 0 4%; }}
h1, h2 {{ text-align: left; -webkit-hyphens: none; hyphens: none; {NO_CORTAR} {NO_SEPARAR} }}
h1 {{ font-size: 1.5em; margin: 0.6em 0 0.2em; line-height: 1.2; }}
h2 {{ font-size: 1.15em; margin: 1.4em 0 0.4em; line-height: 1.25; }}
p {{ margin: 0 0 0.7em; text-align: justify; orphans: 2; widows: 2; }}
.junto {{ {NO_CORTAR} }}
.seccion {{ font-variant: small-caps; letter-spacing: 0.05em; color: #666; margin: 0; {NO_SEPARAR} }}
.bajada {{ font-style: italic; font-size: 1.05em; margin: 0.4em 0 1.2em; }}
.separador {{ text-align: center; margin: 1.2em 0; }}
.cabecera {{ display: block; max-width: 100%; max-height: 45vh; margin: 0 auto 0.6em; }}
figure {{ margin: 1em 0; text-align: center; {NO_CORTAR} }}
figure img {{ max-width: 100%; }}
figcaption {{ font-size: 0.85em; color: #666; font-style: italic; }}
.portada {{ text-align: center; margin-top: 30%; }}
.portada h1 {{ font-size: 2.2em; text-align: center; }}
.mes {{ text-align: center; margin-top: 35%; }}
.mes h1 {{ font-size: 2em; text-align: center; }}
"""
BR = "\x00"          # marca interna de un <br> del sitio; nunca llega al libro
VERSO_MAX = 60       # largo máximo de una línea para tomar el bloque como verso
JUNTO_MAX = 700      # un párrafo más largo no cabe con su título en una pantalla chica
MINUS = "a-záéíóúüñ"


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
            salida.append(BR)
    return _saltos(re.sub(r"\s+", " ", "".join(salida)).strip())


def _saltos(t: str) -> str:
    """El sitio arrastra los <br> de la maqueta impresa: caen en medio de una frase y hasta
    parten una palabra con guion ("su-<br>surro"). En prosa se cambian por un espacio y la
    palabra partida se vuelve a unir; solo se conservan en verso (tres o más líneas cortas)."""
    lineas = [x.strip() for x in t.split(BR)]
    lineas = [x for x in lineas if x]
    if len(lineas) < 2:
        return "".join(lineas)
    if len(lineas) >= 3 and all(len(re.sub(r"<[^>]+>", "", x)) <= VERSO_MAX for x in lineas):
        return "<br/>".join(lineas)
    salida = lineas[0]
    for x in lineas[1:]:
        if re.search(f"[{MINUS}]-$", salida) and re.match(f"[{MINUS}]", x):
            salida = salida[:-1] + x
        else:
            salida += " " + x
    return salida


def _juntar(partes: list[str]) -> list[str]:
    """Cada subtítulo queda en un mismo bloque con el párrafo que lo sigue, para que el cambio
    de página no los separe. Si el párrafo es muy largo, el bloque no cabría en una pantalla
    chica y el lector lo partiría igual: ahí queda solo la regla del CSS."""
    salida, i = [], 0
    while i < len(partes):
        sig = partes[i + 1] if i + 1 < len(partes) else ""
        if partes[i].startswith("<h2>") and sig.startswith("<p>") and len(sig) <= JUNTO_MAX:
            salida.append(f'<div class="junto">{partes[i]}\n{sig}</div>')
            i += 2
        else:
            salida.append(partes[i])
            i += 1
    return salida


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
            if re.fullmatch(r"(\*\s*){3,}", re.sub(r"<[^>]+>", "", t)):
                partes.append('<p class="separador">* * *</p>')
            elif t.strip():
                partes.append(f"<p>{t}</p>")
    return "\n".join(_juntar([p for p in partes if p]))


def pagina(s: requests.Session, url: str) -> BeautifulSoup:
    r = s.get(url, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    if soup.select_one(".um-page-loggedout"):
        sys.exit("Un artículo volvió al login: la sesión no quedó iniciada.")
    return soup


def seccion_de(soup: BeautifulSoup) -> str:
    return _texto(soup.select_one(".post_custom_field.location"))


def es_audio(url: str) -> bool:
    return "/audio-cronica-" in url


def extraer(soup: BeautifulSoup, seccion: str, urls: list[str]) -> dict:
    """Título, bajada, foto de cabecera y cuerpo. Hay dos plantillas: la habitual, con
    .post_content, y una sin campos (texto directo en .l-section-h), que usó el editorial de junio."""
    cuerpo = soup.select_one(".post_content")
    if cuerpo is None:
        secs = [d for d in soup.select(".l-section-h") if d.find_all("p", recursive=False)]
        if not secs:
            sys.exit("Cambió la estructura del sitio: no aparece el texto del artículo.")
        cuerpo = max(secs, key=lambda d: len(d.find_all("p", recursive=False)))
    og = (soup.find("meta", property="og:title") or {}).get("content", "")
    titulo = _texto(soup.select_one(".post_title")) or re.sub(r"\s+[–-]\s+Crónica$", "", og) or seccion
    cabecera = None
    img = soup.select_one(".post_image img")
    src = _src(img) if img is not None else None
    if src is None and soup.select_one(".post_content") is None:
        src = (soup.find("meta", property="og:image") or {}).get("content")
    if src:
        urls.append(src)
        cabecera = len(urls) - 1
    return {
        "titulo": titulo,
        "seccion": seccion,
        "bajada": _texto(soup.select_one(".post_custom_field.foreword")),
        "cabecera": cabecera,
        "cuerpo": _cuerpo(cuerpo, urls),
    }


def numero(s: requests.Session, mes: int, anio: int, urls: list[str]) -> list[dict]:
    """Los artículos de las secciones elegidas, en el orden del sumario.
    Si el número no trae ningún "Editorial" marcado, se toma como editorial el primer
    artículo (sin contar el audio) cuando no tiene sección: es el lugar que ocupa siempre."""
    paginas = [(u, pagina(s, u)) for u in articulos(s, mes, anio) if not es_audio(u)]
    secciones = [seccion_de(p) for _, p in paginas]
    hay_editorial = any(x.lower().startswith("editorial") for x in secciones)
    salida = []
    for i, ((_, soup), sec) in enumerate(zip(paginas, secciones)):
        if not sec and i == 0 and not hay_editorial:
            sec = "Editorial"
        if sec.lower().startswith(SECCIONES):
            salida.append(extraer(soup, sec, urls))
    return salida


def bajar_imagen(s: requests.Session, url: str) -> tuple[bytes, str] | None:
    try:
        r = s.get(url, timeout=30)
        r.raise_for_status()
    except requests.RequestException:
        return None
    tipo = r.headers.get("Content-Type", "").split(";")[0] or mimetypes.guess_type(url)[0] or ""
    if tipo not in EXT:
        return None
    return _achicar(r.content, tipo)


def _achicar(datos: bytes, tipo: str) -> tuple[bytes, str]:
    """Fotos a 1200 px de ancho en JPEG: el libro pesa menos y Telegram acepta hasta 50 MB.
    Sin Pillow, la foto queda tal cual."""
    try:
        from io import BytesIO
        from PIL import Image
    except ImportError:
        return datos, tipo
    try:
        im = Image.open(BytesIO(datos))
        if getattr(im, "is_animated", False):
            return datos, tipo
        im = im.convert("RGB")
        if im.width > 1200:
            im = im.resize((1200, round(im.height * 1200 / im.width)), Image.LANCZOS)
        out = BytesIO()
        im.save(out, "JPEG", quality=82, optimize=True)
    except Exception:
        return datos, tipo
    return (out.getvalue(), "image/jpeg") if out.tell() < len(datos) else (datos, tipo)


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
                # Foto, sección, título y bajada en un solo bloque: el título no se corta.
                partes = ['<div class="junto">']
                if a["cabecera"] is not None:
                    partes.append(f'<img class="cabecera" src="IMG{a["cabecera"]}" alt=""/>')
                partes.append(f'<p class="seccion">{html.escape(a["seccion"])}</p>')
                partes.append(f'<h1>{html.escape(a["titulo"])}</h1>')
                if a["bajada"]:
                    partes.append(f'<p class="bajada">{html.escape(a["bajada"])}</p>')
                partes.append('</div>')
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


def _control_diseno(destino: Path) -> None:
    """En este PC el libro pasa por el control de diseño de Laboral, que lo mide paginado
    (títulos cortados o solos al pie, saltos de línea que sobran) y falla si encuentra algo.
    En GitHub Actions ese control no existe: allá las mismas reglas las cubre --autotest."""
    lib = Path.home() / "Desktop" / "Personal" / "Claude" / "Laboral" / "lib"
    if not (lib / "auditar_epub.py").exists():
        return
    sys.path.insert(0, str(lib))
    from auditar_epub import exigir_diseno
    exigir_diseno(destino)


def generar(anio: int, salida: Path) -> tuple[Path, list[str]]:
    """Arma el libro del año con todos los números publicados. Devuelve la ruta y los meses."""
    s = sesion()
    hoy = date.today()
    ultimo = 12 if anio < hoy.year else hoy.month
    urls: list[str] = []
    numeros: list[tuple[str, list[dict]]] = []
    for mes in range(1, ultimo + 1):
        arts = numero(s, mes, anio, urls)
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
    _control_diseno(destino)
    # Solo conteos: los registros de Actions son públicos.
    total = sum(len(a) for _, a in numeros)
    print(f"Crónica {anio}: {len(numeros)} números, {total} artículos, "
          f"{len(imagenes)}/{len(urls)} imágenes -> {destino.name}")
    return destino, [m for m, _ in numeros]


# ---------- envío automático (GitHub Actions, después de cronica.py) ----------

def enviar_documento(ruta: Path, texto: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        sys.exit("ERROR: faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID.")
    for i in range(3):
        try:
            with ruta.open("rb") as f:
                r = requests.post(f"https://api.telegram.org/bot{token}/sendDocument",
                                  data={"chat_id": chat, "caption": texto},
                                  files={"document": (ruta.name, f, "application/epub+zip")},
                                  timeout=180)
            if r.ok and r.json().get("ok"):
                return True
            print(f"Telegram, intento {i + 1}: HTTP {r.status_code}")
        except requests.RequestException as ex:
            print(f"Telegram, intento {i + 1} falló: {type(ex).__name__}")
    return False


def enviar_pendientes(salida: Path) -> None:
    """Rearma y manda el libro del año cuando hay números con libro=false en cronica.json.
    Espera un día desde que se detectó el número, porque el sitio lo carga durante una mañana.
    Idempotente: si Telegram falla, quedan en false y se reintenta en la corrida siguiente."""
    import json
    archivo = Path(__file__).with_name("cronica.json")
    numeros = json.loads(archivo.read_text(encoding="utf-8"))
    hoy = date.today().isoformat()
    for n in numeros:
        n.setdefault("detectado", hoy)
    listos = [n for n in numeros if not n.get("libro") and n["detectado"] < hoy]
    for anio in sorted({int(n["fecha"][:4]) for n in listos}):
        ruta, meses = generar(anio, salida)
        nuevos = ", ".join(n["nombre"].replace("Crónica ", "") for n in listos if n["fecha"].startswith(str(anio)))
        texto = f"📚 Crónica {anio} (Editorial y Junto al Padre), con {nuevos}. {len(meses)} números en total."
        if enviar_documento(ruta, texto):
            for n in numeros:
                if n["fecha"].startswith(str(anio)):
                    n["libro"] = True
            print(f"Libro {anio} enviado.")
        else:
            print(f"ERROR: el libro {anio} no se pudo enviar; se reintenta mañana.")
    archivo.write_text(json.dumps(numeros, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if not listos:
        print("Sin números nuevos para el libro.")


def autotest() -> None:
    """Sin red: los saltos de línea de la maqueta y los títulos pegados a su texto."""
    def cuerpo(h: str) -> str:
        return _cuerpo(BeautifulSoup(f"<div>{h}</div>", "html.parser").div, [])

    largo = "palabra " * 30
    casos = [
        # <br> en medio de una frase: espacio
        (f"<p>{largo}la frase<br> sigue en la otra línea.</p>", "la frase sigue en la otra", "<br"),
        # palabra partida con guion: se vuelve a unir
        (f"<p>{largo}una pala-<br> bra partida.</p>", "una palabra partida", "pala-"),
        # <br> después de un punto, en prosa: tampoco queda
        (f"<p>{largo}termina aquí.<br> Y sigue aquí.</p>", "aquí. Y sigue", "<br"),
        # verso: se conserva
        ("<p>Uno de los versos<br>otro verso corto<br>y el último</p>", "versos<br/>otro", None),
        # subtítulo en el mismo bloque que su párrafo
        ("<h2>Subtítulo</h2><p>Texto.</p>", '<div class="junto"><h2>Subtítulo</h2>\n<p>Texto.</p></div>', None),
        # separador
        ("<p><strong><em>* * *</em></strong></p>", '<p class="separador">* * *</p>', None),
    ]
    for entrada, debe, no_debe in casos:
        x = cuerpo(entrada)
        assert debe in x, f"falta {debe!r} en {x[-120:]!r}"
        assert no_debe is None or no_debe not in x, f"sobra {no_debe!r} en {x[-120:]!r}"
        assert BR not in x
    for regla in ("break-after: avoid", "break-inside: avoid"):
        assert regla in CSS.split("h1, h2 {")[1].split("}")[0], f"el CSS de los títulos perdió {regla}"
    print(f"autotest del libro: {len(casos)} casos y el CSS de los títulos, bien.")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("anio", type=int, nargs="?", help="2026")
    ap.add_argument("--salida", type=Path, default=SALIDA)
    ap.add_argument("--enviar-pendientes", action="store_true",
                    help="modo Actions: rearma y manda por Telegram el libro de los números nuevos")
    ap.add_argument("--autotest", action="store_true",
                    help="prueba sin red los saltos de línea y los títulos")
    a = ap.parse_args()
    if a.autotest:
        autotest()
    elif a.enviar_pendientes:
        enviar_pendientes(a.salida)
    elif a.anio:
        generar(a.anio, a.salida)
    else:
        ap.error("falta el año o --enviar-pendientes")


if __name__ == "__main__":
    main()
