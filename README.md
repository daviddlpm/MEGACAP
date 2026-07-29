# Megacap · panel de valoración

Panel estático para **GOOGL, MSFT, META y AMZN**: precio, PER, EV/EBITDA, EV/FCF, BPA,
deuda neta, márgenes, ROIC, free cash flow y titulares. Se despliega en GitHub Pages y se
actualiza solo mediante GitHub Actions.

## Cómo funciona

GitHub Pages solo sirve ficheros estáticos, así que el navegador no puede llamar a una API
de pago sin exponer la clave ni saltarse CORS. Por eso el trabajo pesado ocurre antes:

```
GitHub Actions (cron)                Repositorio            GitHub Pages
─────────────────────                ───────────            ────────────
scripts/fetch_data.py  ──descarga──▶  data/*.json  ──lee──▶  index.html
   clave en Secrets                    (commit)              sin CORS, sin clave
```

- **Actualización diaria** tras el cierre de Wall Street, y **cada hora durante la sesión**
  americana. Ambos cron están en `.github/workflows/actualizar-datos.yml`.
- El botón **Refrescar precio** de la cabecera pide la última cotización a Stooq
  directamente desde el navegador. Es el único dato verdaderamente en vivo; todo lo demás
  procede del último commit de datos.

## Puesta en marcha

1. **Crear el repositorio** y subir estos ficheros a la rama `main`.

2. **Obtener una clave** de [Financial Modeling Prep](https://site.financialmodelingprep.com/developer/docs).
   El plan gratuito cubre los cuatro valores; los planes de pago dan más histórico y noticias.

3. **Guardar la clave como secreto**
   `Settings ▸ Secrets and variables ▸ Actions ▸ New repository secret`
   - Nombre: `FMP_API_KEY`
   - Valor: tu clave

4. **Permitir que Actions escriba en el repositorio**
   `Settings ▸ Actions ▸ General ▸ Workflow permissions ▸ Read and write permissions`

5. **Activar Pages**
   `Settings ▸ Pages ▸ Source: Deploy from a branch ▸ main / (root)`

6. **Lanzar la primera descarga**
   `Actions ▸ Actualizar datos ▸ Run workflow`. Tarda un par de minutos y deja los
   `data/*.json` commiteados. A partir de ahí la web ya carga con datos.

La URL queda en `https://<usuario>.github.io/<repositorio>/`.

## Ejecutar en local

```bash
export FMP_API_KEY="tu_clave"
python scripts/fetch_data.py
python -m http.server 8000     # abre http://localhost:8000
```

Sin `FMP_API_KEY` el script sigue funcionando pero solo descarga precios desde Stooq: las
gráficas de múltiplos aparecerán vacías.

## Estructura

| Ruta | Qué hace |
|---|---|
| `index.html` | Estructura de la página |
| `assets/style.css` | Estilos |
| `assets/app.js` | Carga de datos, gráficas (Chart.js) y bandas de valoración |
| `scripts/fetch_data.py` | Descarga y normaliza los datos |
| `data/<TICKER>.json` | Serie de precios, fundamentales anuales y trimestrales, noticias |
| `data/index.json` | Resumen de los cuatro valores para la tabla comparativa |

## Añadir o cambiar valores

Edita `TICKERS` y `NOMBRES` en `scripts/fetch_data.py`, y `COLOR` en `assets/app.js`.
El resto de la página se genera a partir del índice, así que no hay que tocar el HTML.

## Notas sobre los datos

- Los múltiplos anuales se calculan sobre el valor de empresa del cierre de cada ejercicio,
  no sobre el precio actual: por eso la serie histórica de PER no coincide con el PER TTM
  de la cabecera.
- Un múltiplo aparece vacío cuando el denominador fue negativo ese año (EBITDA o FCF en
  pérdidas). Es intencionado: un EV/EBITDA negativo no es interpretable.
- El script tolera fallos parciales. Si una descarga falla, conserva el dato anterior y lo
  anota en el campo `avisos`, que la página muestra bajo el precio.
- FMP está migrando de las rutas `/api/v3/` a `/stable/`. El script prueba primero la nueva
  y cae a la antigua, y acepta varios nombres de campo para el mismo dato.

---

Panel informativo. No constituye asesoramiento de inversión.
