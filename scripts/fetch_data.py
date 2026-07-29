#!/usr/bin/env python3
"""
Descarga y normaliza métricas de GOOGL, MSFT, META y AMZN.

Salida: data/<TICKER>.json  +  data/index.json

Fuentes
-------
1. Financial Modeling Prep (FMP)  -> fundamentales, múltiplos, noticias, precio EOD.
   Requiere la variable de entorno FMP_API_KEY (GitHub Secret).
   Se intenta primero la ruta /stable/ y, si falla, la ruta legacy /api/v3/.
2. Stooq (sin clave, CSV abierto) -> respaldo de serie de precios si FMP no responde.

El script nunca aborta por un fallo puntual: si una sección no se puede descargar
conserva el valor previo del JSON ya existente y lo marca en "avisos".
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TICKERS = ["GOOGL", "MSFT", "META", "AMZN"]

NOMBRES = {
    "GOOGL": "Alphabet Inc.",
    "MSFT": "Microsoft Corporation",
    "META": "Meta Platforms, Inc.",
    "AMZN": "Amazon.com, Inc.",
}

RAIZ = Path(__file__).resolve().parent.parent
DIR_DATOS = RAIZ / "data"

API_KEY = os.environ.get("FMP_API_KEY", "").strip()
BASE_STABLE = "https://financialmodelingprep.com/stable"
BASE_LEGACY = "https://financialmodelingprep.com/api/v3"

ANOS_HISTORICO = 12          # ejercicios anuales a conservar
TRIMESTRES_HISTORICO = 24    # trimestres a conservar (6 años)
PAUSA = 0.35                 # segundos entre llamadas, para no saturar el plan gratuito


# --------------------------------------------------------------------------- #
# utilidades HTTP
# --------------------------------------------------------------------------- #

def _get(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": "megacap-tracker/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fmp(ruta_stable: str, ruta_legacy: str, **params):
    """Llama a FMP probando la ruta stable y cayendo a la legacy si hace falta."""
    if not API_KEY:
        return None

    intentos = [
        f"{BASE_STABLE}/{ruta_stable}?" + urllib.parse.urlencode({**params, "apikey": API_KEY}),
    ]
    # La API legacy pone el símbolo en el path, no como query param.
    simbolo = params.get("symbol")
    if ruta_legacy and simbolo:
        legacy_params = {k: v for k, v in params.items() if k != "symbol"}
        intentos.append(
            f"{BASE_LEGACY}/{ruta_legacy}/{simbolo}?"
            + urllib.parse.urlencode({**legacy_params, "apikey": API_KEY})
        )

    for url in intentos:
        try:
            datos = json.loads(_get(url))
            if isinstance(datos, dict) and datos.get("Error Message"):
                continue
            if datos:
                time.sleep(PAUSA)
                return datos
        except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
            continue
        finally:
            time.sleep(PAUSA)
    return None


def campo(d: dict, *alias, defecto=None):
    """Devuelve el primer alias presente y no nulo. Absorbe cambios de esquema de FMP."""
    if not isinstance(d, dict):
        return defecto
    for a in alias:
        if a in d and d[a] is not None:
            return d[a]
    return defecto


def num(v):
    try:
        f = float(v)
        return f if f == f and abs(f) != float("inf") else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# respaldo de precios: Stooq
# --------------------------------------------------------------------------- #

def precios_stooq(ticker: str):
    """Serie diaria completa desde Stooq. Sin clave y con CORS abierto."""
    url = f"https://stooq.com/q/d/l/?s={ticker.lower()}.us&i=d"
    try:
        texto = _get(url).decode("utf-8", "replace")
    except Exception:
        return []
    filas = list(csv.DictReader(io.StringIO(texto)))
    salida = []
    for f in filas:
        c = num(f.get("Close"))
        if f.get("Date") and c:
            salida.append({"f": f["Date"], "c": round(c, 4), "v": num(f.get("Volume")) or 0})
    return salida


# --------------------------------------------------------------------------- #
# bloques de datos
# --------------------------------------------------------------------------- #

def serie_precios(ticker: str):
    datos = fmp("historical-price-eod/full", "historical-price-full",
                symbol=ticker, serietype="line")
    filas = None
    if isinstance(datos, dict):
        filas = datos.get("historical")
    elif isinstance(datos, list):
        filas = datos

    if filas:
        salida = []
        for f in filas:
            c = num(campo(f, "close", "adjClose", "price"))
            fecha = campo(f, "date")
            if fecha and c:
                salida.append({"f": str(fecha)[:10], "c": round(c, 4),
                               "v": num(campo(f, "volume")) or 0})
        salida.sort(key=lambda x: x["f"])
        if salida:
            return salida, "FMP"

    stooq = precios_stooq(ticker)
    return stooq, "Stooq" if stooq else "sin datos"


def cotizacion(ticker: str):
    d = fmp("quote", "quote", symbol=ticker)
    if isinstance(d, list) and d:
        d = d[0]
    if not isinstance(d, dict):
        return {}
    return {
        "precio": num(campo(d, "price")),
        "cambio": num(campo(d, "change")),
        "cambioPct": num(campo(d, "changePercentage", "changesPercentage")),
        "capitalizacion": num(campo(d, "marketCap")),
        "volumen": num(campo(d, "volume")),
        "max52": num(campo(d, "yearHigh")),
        "min52": num(campo(d, "yearLow")),
        "per": num(campo(d, "pe")),
        "bpaTTM": num(campo(d, "eps")),
    }


def _serie_fundamental(ticker: str, periodo: str):
    """Une key-metrics + ratios + cuentas en una sola serie temporal ordenada."""
    limite = ANOS_HISTORICO if periodo == "annual" else TRIMESTRES_HISTORICO

    km = fmp("key-metrics", "key-metrics", symbol=ticker, period=periodo, limit=limite) or []
    ra = fmp("ratios", "ratios", symbol=ticker, period=periodo, limit=limite) or []
    pg = fmp("income-statement", "income-statement", symbol=ticker, period=periodo, limit=limite) or []
    bl = fmp("balance-sheet-statement", "balance-sheet-statement", symbol=ticker, period=periodo, limit=limite) or []
    cf = fmp("cash-flow-statement", "cash-flow-statement", symbol=ticker, period=periodo, limit=limite) or []
    ev = fmp("enterprise-values", "enterprise-values", symbol=ticker, period=periodo, limit=limite) or []

    def indexar(lista):
        return {str(campo(x, "date", defecto=""))[:10]: x for x in lista if isinstance(x, dict)}

    km_i, ra_i, pg_i, bl_i, cf_i, ev_i = map(indexar, (km, ra, pg, bl, cf, ev))
    fechas = sorted(set().union(km_i, ra_i, pg_i, bl_i, cf_i, ev_i))

    serie = []
    for f in fechas:
        k, r, p, b, c, e = (km_i.get(f, {}), ra_i.get(f, {}), pg_i.get(f, {}),
                            bl_i.get(f, {}), cf_i.get(f, {}), ev_i.get(f, {}))

        ingresos = num(campo(p, "revenue"))
        ebitda = num(campo(p, "ebitda", "EBITDA"))
        beneficio = num(campo(p, "netIncome"))
        bpa = num(campo(p, "epsdiluted", "epsDiluted", "eps"))
        margen_op = num(campo(p, "operatingIncomeRatio"))

        deuda_total = num(campo(b, "totalDebt"))
        caja = num(campo(b, "cashAndCashEquivalents", "cashAndShortTermInvestments"))
        deuda_neta = num(campo(b, "netDebt", "netDebtToEBITDA"))
        if deuda_neta is None and deuda_total is not None and caja is not None:
            deuda_neta = deuda_total - caja

        fco = num(campo(c, "operatingCashFlow", "netCashProvidedByOperatingActivities"))
        capex = num(campo(c, "capitalExpenditure"))
        fcl = num(campo(c, "freeCashFlow"))
        if fcl is None and fco is not None and capex is not None:
            fcl = fco + capex  # capex viene en negativo

        valor_empresa = num(campo(e, "enterpriseValue")) or num(campo(k, "enterpriseValue"))

        ev_ebitda = num(campo(k, "evToEBITDA", "enterpriseValueOverEBITDA", "evToOperatingCashFlow"))
        if ev_ebitda is None and valor_empresa and ebitda:
            ev_ebitda = valor_empresa / ebitda

        ev_fcl = num(campo(k, "evToFreeCashFlow", "enterpriseValueOverFreeCashFlow"))
        if ev_fcl is None and valor_empresa and fcl:
            ev_fcl = valor_empresa / fcl

        per = num(campo(r, "priceToEarningsRatio", "priceEarningsRatio", "peRatio"))
        if per is None:
            per = num(campo(k, "peRatio"))

        dn_ebitda = num(campo(k, "netDebtToEBITDA"))
        if dn_ebitda is None and deuda_neta is not None and ebitda:
            dn_ebitda = deuda_neta / ebitda

        serie.append({
            "f": f,
            "ejercicio": campo(p, "calendarYear", "fiscalYear") or f[:4],
            "periodo": campo(p, "period") or ("FY" if periodo == "annual" else ""),
            "ingresos": ingresos,
            "ebitda": ebitda,
            "beneficio": beneficio,
            "bpa": bpa,
            "margenOperativo": margen_op,
            "margenNeto": (beneficio / ingresos) if (beneficio and ingresos) else None,
            "fcl": fcl,
            "capex": capex,
            "deudaTotal": deuda_total,
            "deudaNeta": deuda_neta,
            "caja": caja,
            "valorEmpresa": valor_empresa,
            "per": per,
            "evEbitda": ev_ebitda,
            "evFcl": ev_fcl,
            "deudaNetaEbitda": dn_ebitda,
            "roic": num(campo(k, "returnOnInvestedCapital", "roic")),
            "roe": num(campo(r, "returnOnEquity")),
        })
    return serie


def noticias(ticker: str, limite: int = 8):
    d = (fmp("news/stock", "stock_news", symbols=ticker, symbol=ticker, limit=limite)
         or fmp("news/stock-latest", "", symbols=ticker, limit=limite) or [])
    salida = []
    for n in d if isinstance(d, list) else []:
        salida.append({
            "titulo": campo(n, "title"),
            "fecha": str(campo(n, "publishedDate", "date", defecto=""))[:16],
            "medio": campo(n, "publisher", "site"),
            "url": campo(n, "url", "link"),
        })
    return [n for n in salida if n["titulo"] and n["url"]][:limite]


# --------------------------------------------------------------------------- #
# orquestación
# --------------------------------------------------------------------------- #

def anterior(ticker: str) -> dict:
    ruta = DIR_DATOS / f"{ticker}.json"
    if ruta.exists():
        try:
            return json.loads(ruta.read_text("utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def procesar(ticker: str) -> dict:
    print(f"-> {ticker}", flush=True)
    previo = anterior(ticker)
    avisos = []

    precios, fuente = serie_precios(ticker)
    if not precios:
        precios = previo.get("precios", [])
        avisos.append("serie de precios sin actualizar")

    q = cotizacion(ticker)
    if not q.get("precio") and precios:
        q["precio"] = precios[-1]["c"]
        if len(precios) > 1:
            q["cambio"] = precios[-1]["c"] - precios[-2]["c"]
            q["cambioPct"] = q["cambio"] / precios[-2]["c"] * 100
        avisos.append("cotización derivada de la serie diaria")

    anual = _serie_fundamental(ticker, "annual")
    if not anual:
        anual = previo.get("anual", [])
        avisos.append("fundamentales anuales sin actualizar")

    trimestral = _serie_fundamental(ticker, "quarter")
    if not trimestral:
        trimestral = previo.get("trimestral", [])

    prensa = noticias(ticker) or previo.get("noticias", [])

    return {
        "ticker": ticker,
        "nombre": NOMBRES[ticker],
        "actualizado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fuentePrecios": fuente,
        "cotizacion": q,
        "precios": precios[-2600:],   # ~10 años de sesiones
        "anual": anual,
        "trimestral": trimestral,
        "noticias": prensa,
        "avisos": avisos,
    }


def main() -> int:
    DIR_DATOS.mkdir(parents=True, exist_ok=True)
    if not API_KEY:
        print("AVISO: FMP_API_KEY no definida. Solo se descargarán precios desde Stooq.",
              file=sys.stderr)

    resumen = []
    for t in TICKERS:
        try:
            d = procesar(t)
        except Exception as e:  # noqa: BLE001 — un ticker roto no debe tumbar el workflow
            print(f"ERROR en {t}: {e}", file=sys.stderr)
            d = anterior(t)
            if not d:
                continue
            d["avisos"] = (d.get("avisos") or []) + [f"fallo de descarga: {e}"]

        (DIR_DATOS / f"{t}.json").write_text(
            json.dumps(d, ensure_ascii=False, separators=(",", ":")), "utf-8"
        )

        ult = (d.get("anual") or [{}])[-1]
        resumen.append({
            "ticker": t,
            "nombre": d.get("nombre"),
            "precio": (d.get("cotizacion") or {}).get("precio"),
            "cambioPct": (d.get("cotizacion") or {}).get("cambioPct"),
            "capitalizacion": (d.get("cotizacion") or {}).get("capitalizacion"),
            "per": (d.get("cotizacion") or {}).get("per") or ult.get("per"),
            "evEbitda": ult.get("evEbitda"),
            "evFcl": ult.get("evFcl"),
            "bpa": ult.get("bpa"),
            "deudaNeta": ult.get("deudaNeta"),
            "deudaNetaEbitda": ult.get("deudaNetaEbitda"),
            "margenOperativo": ult.get("margenOperativo"),
            "avisos": d.get("avisos") or [],
        })

    (DIR_DATOS / "index.json").write_text(
        json.dumps({
            "actualizado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tickers": TICKERS,
            "resumen": resumen,
        }, ensure_ascii=False, indent=1),
        "utf-8",
    )
    print(f"Listo: {len(resumen)} valores escritos en {DIR_DATOS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
