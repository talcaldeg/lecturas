# Lecturas

Lista de los mensajes del Prelado publicados en opusdei.org/es-cl, con la marca de leído
guardada en el navegador. Página: https://talcaldeg.github.io/lecturas/

- `revisar.py` corre a diario en GitHub Actions, lee el canal
  `https://opusdei.org/es-cl/lastarticles.xml`, agrega los nuevos a `mensajes.json` y avisa
  por Telegram al grupo "Lecturas" (secrets `TELEGRAM_BOT_TOKEN`, el mismo bot de las alertas de
  empleo, y `TELEGRAM_CHAT_ID`, el id del grupo, distinto del chat de empleos).
- `index.html` lee `mensajes.json` y muestra primero los pendientes.
- Las páginas del sitio piden verificación de navegador (Cloudflare); el canal no, por eso se lee
  el canal. Los mensajes anteriores a esta herramienta se cargaron a mano desde la etiqueta
  "Mensaje Fernando Ocáriz".

## Crónica

Números mensuales de Crónica (cronicadigital.org), con la misma marca de leído. Página:
https://talcaldeg.github.io/lecturas/cronica.html

- `cronica.py` corre en el mismo workflow, después de `revisar.py`, y avisa al mismo grupo.
- No inicia sesión: la API pública de WordPress (`/wp-json/wp/v2/categories`) lista cada número
  como una categoría "<Mes> <año>" con su conteo de artículos; los meses se crean por adelantado
  con 0, y un número salió cuando el conteo pasa de 0.
- El contenido de la revista es restringido y esta página es pública: `cronica.json` guarda solo
  el mes, el año y el enlace al número, nunca títulos ni textos de artículos.
- Se lista desde enero de 2026 (`DESDE` en `cronica.py`).
