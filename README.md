# Lecturas

Lista de los mensajes del Prelado publicados en opusdei.org/es-cl, con la marca de leído
guardada en el navegador. Página: https://talcaldeg.github.io/lecturas/

- `revisar.py` corre a diario en GitHub Actions, lee el canal
  `https://opusdei.org/es-cl/lastarticles.xml`, agrega los nuevos a `mensajes.json` y avisa
  por Telegram (secrets `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`, los mismos del bot de
  alertas de empleo).
- `index.html` lee `mensajes.json` y muestra primero los pendientes.
- Las páginas del sitio piden verificación de navegador (Cloudflare); el canal no, por eso se lee
  el canal. Los mensajes anteriores a esta herramienta se cargaron a mano desde la etiqueta
  "Mensaje Fernando Ocáriz".
