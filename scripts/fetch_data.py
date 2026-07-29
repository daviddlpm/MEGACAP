#!/usr/bin/env python3
"""
Descarga y normaliza métricas de GOOGL, MSFT, META y AMZN.

Uso
---
    python scripts/fetch_data.py            # descarga completa
    python scripts/fetch_data.py --precios  # solo precio y cotización (ligero)

Presupuesto de llamadas (plan gratuito de FMP: 250/día)
-------------------------------------------------------
    --precios   2 por valor  ->   8 por ejecución
    completa    5 por valor  ->  20 por ejecución

Variables de entorno
--------------------
    FMP_API_KEY   obligatoria para los fundamentales
    FMP_ANOS      ejercicios a pedir (por defecto 5, el máximo del plan gratuito)
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
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
ANOS = int(os.environ.get("FMP_ANOS", "5"))
BASE = "https://financialmodelingprep.com/stable"
BASE_LEGACY = "https://financialmodelingprep.com/api/v3"
PAUSA = 0.4

contador = {"ok": 0, "fallo": 0}


# --------------------------------------------------------------------------- #
# capa HTTP
# --------------------------------------------------------------------------- #

def _get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "megacap-tracker/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fmp(ruta: str, ruta_legacy: str = "", **params):
    """Llama a FMP. Registra en consola qué falla y por qué."""
    if not API_KEY:
        return None

    simbolo = params.get("symbol", "")
    urls = [f"{BASE}/{ruta}?" + urllib.parse.urlencode({**params, "apikey": API_KEY})]
    if ruta_legacy and simbolo:
        resto = {k: v for k, v in params.items() if k != "symbol"}
        urls.append(f"{BASE_LEGACY}/{ruta_legacy}/{simbolo}?"
                    + urllib.parse.urlencode({**resto, "apikey": API_KEY}))

    ultimo_error = "sin respuesta"
    for url in urls:
        publica = url.split("&apikey=")[0].split("?apikey=")[0]
        try:
            datos = json.loads(_get(url))
            if isinstance(datos, dict) and (datos.get("Error Message") or datos.get("error")):
                ultimo_error = str(datos.get("Error Message") or datos.get("error"))[:160]
            elif datos:
                contador["ok"] += 1
                return datos
            else:
                ultimo_error = "respuesta vacía"
        except urllib.error.HTTPError as e:
            cuerpo = ""
            try:
                cuerpo = e.read().decode("utf-8", "replace")[:160]
            except Exception:
                pass
            ultimo_error = f"HTTP {e.code} {cuerpo}"
        except Exception as e:  # noqa: BLE001
            ultimo_error = f"{type(e).__name__}: {e}"
        finally:
            time.sleep(PAUSA)

        print(f"   ! {publica} -> {ultimo_error}", file=sys.stderr)

    contador["fallo"] += 1
    return None


def campo(d, *alias, defecto=None):
    if not isinstance(d, dict):
        return defecto
    for a in alias:
        if d.get(a) is not None:
            return d[a]
    return defecto


def num(v):
    try:
        f = float(v)
        return f if f == f and abs(f) != float("inf") else None
    except (TypeError, ValueError):
        return None


def div(a, b):
    a, b = num(a), num(b)
    return a / b if (a is not None and b) else None


# --------------------------------------------------------------------------- #
# precios
# --------------------------------------------------------------------------- #

def precios_stooq(ticker: str):
    try:
        texto = _get(f"https://stooq.com/q/d/l/?s={ticker.lower()}.us&i=d").decode("utf-8", "replace")
    except Exception:
        return []
    salida = []
    for f in csv.DictReader(io.StringIO(texto)):
        c = num(f.get("Close"))
        if f.get("Date") and c:
            salida.append({"f": f["Date"], "c": round(c, 4), "v": num(f.get("Volume")) or 0})
    return salida


def serie_precios(ticker: str):
    d = fmp("historical-price-eod/light", "historical-price-full", symbol=ticker)
    filas = d.get("historical") if isinstance(d, dict) else d
    if filas:
        salida = []
        for f in filas:
            c = num(campo(f, "close", "adjClose", "price"))
            fecha = campo(f, "date")
            if fecha and c:
                salida.append({"f": str(fecha)[:10], "c": round(c, 4), "v": num(campo(f, "volume")) or 0})
        salida.sort(key=lambda x: x["f"])
        if salida:
            return salida, "FMP"

    s = precios_stooq(ticker)
    return s, ("Stooq" if s else "sin datos")


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


# --------------------------------------------------------------------------- #
# fundamentales: 3 llamadas por valor
# --------------------------------------------------------------------------- #

def fundamentales(ticker: str):
    km = fmp("key-metrics", "key-metrics", symbol=ticker, period="annual", limit=ANOS) or []
    pg = fmp("income-statement", "income-statement", symbol=ticker, period="annual", limit=ANOS) or []
    bl = fmp("balance-sheet-statement", "balance-sheet-statement", symbol=ticker, period="annual", limit=ANOS) or []
    cf = fmp("cash-flow-statement", "cash-flow-statement", symbol=ticker, period="annual", limit=ANOS) or []

    def idx(l):
        return {str(campo(x, "date", defecto=""))[:10]: x for x in l if isinstance(x, dict)}

    km_i, pg_i, bl_i, cf_i = idx(km), idx(pg), idx(bl), idx(cf)
    serie = []

    for f in sorted(set().union(km_i, pg_i, bl_i, cf_i)):
        k, p, b, c = km_i.get(f, {}), pg_i.get(f, {}), bl_i.get(f, {}), cf_i.get(f, {})

        ingresos = num(campo(p, "revenue"))
        ebitda = num(campo(p, "ebitda", "EBITDA"))
        beneficio = num(campo(p, "netIncome"))
        capitalizacion = num(campo(k, "marketCap"))

        # PER: el rendimiento sobre beneficio de key-metrics es el camino más fiable.
        rendimiento = num(campo(k, "earningsYield"))
        per = (1 / rendimiento) if (rendimiento and rendimiento > 0) else div(capitalizacion, beneficio)

        # Deuda neta calculada aquí y no tomada de FMP: su campo netDebt resta solo
        # la caja inmediata, e ignora los valores negociables. Para estas cuatro
        # compañías, que guardan casi toda su liquidez en cartera de renta fija, eso
        # convierte posiciones de caja neta en falso endeudamiento.
        deuda_total = num(campo(b, "totalDebt"))
        efectivo = num(campo(b, "cashAndCashEquivalents"))
        inversiones_cp = num(campo(b, "shortTermInvestments")) or 0
        caja = num(campo(b, "cashAndShortTermInvestments"))
        if caja is None and efectivo is not None:
            caja = efectivo + inversiones_cp

        if deuda_total is not None and caja is not None:
            deuda_neta = deuda_total - caja
        else:
            deuda_neta = num(campo(b, "netDebt"))

        fco = num(campo(c, "operatingCashFlow", "netCashProvidedByOperatingActivities"))
        capex = num(campo(c, "capitalExpenditure"))
        fcl = num(campo(c, "freeCashFlow"))
        if fcl is None and fco is not None and capex is not None:
            fcl = fco + capex  # el capex llega en negativo

        valor_empresa = num(campo(k, "enterpriseValue"))

        serie.append({
            "f": f,
            "ejercicio": str(campo(k, "fiscalYear") or campo(p, "fiscalYear", "calendarYear") or f[:4]),
            "ingresos": ingresos,
            "ebitda": ebitda,
            "beneficio": beneficio,
            "bpa": num(campo(p, "epsDiluted", "epsdiluted", "eps")),
            "margenOperativo": num(campo(p, "operatingIncomeRatio")) or div(campo(p, "operatingIncome"), ingresos),
            "margenNeto": div(beneficio, ingresos),
            "fcl": fcl,
            "capex": capex,
            "deudaTotal": deuda_total,
            "deudaNeta": deuda_neta,
            "caja": caja,
            "valorEmpresa": valor_empresa,
            "capitalizacion": capitalizacion,
            "per": per,
            "evEbitda": num(campo(k, "evToEBITDA")) or div(valor_empresa, ebitda),
            "evFcl": num(campo(k, "evToFreeCashFlow")) or div(valor_empresa, fcl),
            "deudaNetaEbitda": num(campo(k, "netDebtToEBITDA")) or div(deuda_neta, ebitda),
            "roic": num(campo(k, "returnOnInvestedCapital")),
            "roe": num(campo(k, "returnOnEquity")),
            "acciones": div(beneficio, num(campo(p, "epsDiluted", "epsdiluted", "eps"))),
            "margenFcl": div(fcl, ingresos),
        })

    # crecimiento interanual de ingresos, una vez ordenada la serie
    for i, x in enumerate(serie):
        prev = serie[i - 1]["ingresos"] if i else None
        x["crecimientoIngresos"] = ((x["ingresos"] / prev - 1)
                                    if (prev and x["ingresos"]) else None)
    return serie


# --------------------------------------------------------------------------- #
# múltiplos corrientes (TTM)
# --------------------------------------------------------------------------- #

def ttm(ticker: str, q: dict, anual: list) -> dict:
    """
    Múltiplos con la capitalización de HOY sobre los últimos doce meses.

    Los ratios anuales de FMP usan la capitalización del cierre de cada ejercicio,
    así que no sirven para saber a cuánto cotiza la empresa ahora. Aquí se pide
    primero el bloque TTM del proveedor y, si no está disponible en el plan, se
    reconstruye con el precio actual y los últimos fundamentales publicados.
    """
    km = fmp("key-metrics-ttm", "key-metrics-ttm", symbol=ticker)
    ra = fmp("ratios-ttm", "ratios-ttm", symbol=ticker)
    km = km[0] if isinstance(km, list) and km else (km if isinstance(km, dict) else {})
    ra = ra[0] if isinstance(ra, list) and ra else (ra if isinstance(ra, dict) else {})

    u = anual[-1] if anual else {}
    capitalizacion = num(q.get("capitalizacion"))
    deuda_neta = num(u.get("deudaNeta"))
    valor_empresa = (capitalizacion + deuda_neta) if (capitalizacion is not None and deuda_neta is not None) else None

    # BPA: el de los últimos doce meses manda sobre el del ejercicio cerrado.
    bpa = num(q.get("bpaTTM")) or num(campo(ra, "netIncomePerShareTTM")) or num(u.get("bpa"))

    ebitda = num(campo(km, "ebitdaTTM")) or num(u.get("ebitda"))
    fcl = num(campo(km, "freeCashFlowTTM")) or num(u.get("fcl"))
    beneficio = num(campo(km, "netIncomeTTM")) or num(u.get("beneficio"))

    per = (num(q.get("per"))
           or num(campo(ra, "priceToEarningsRatioTTM", "peRatioTTM"))
           or div(q.get("precio"), bpa)
           or div(capitalizacion, beneficio))

    return {
        "base": "capitalización actual sobre últimos doce meses",
        "ejercicioBase": u.get("ejercicio"),
        "bpa": bpa,
        "valorEmpresa": valor_empresa,
        "per": per,
        "evEbitda": num(campo(km, "evToEBITDATTM")) or div(valor_empresa, ebitda),
        "evFcl": num(campo(km, "evToFreeCashFlowTTM")) or div(valor_empresa, fcl),
        "deudaNetaEbitda": div(deuda_neta, ebitda),
        "margenOperativo": num(u.get("margenOperativo")),
        "crecimientoIngresos": num(u.get("crecimientoIngresos")),
        "acciones": num(u.get("acciones")),
        "cajaNeta": (-deuda_neta) if deuda_neta is not None else None,
        "roic": num(campo(km, "returnOnInvestedCapitalTTM")) or num(u.get("roic")),
        "deudaNeta": deuda_neta,
    }


# --------------------------------------------------------------------------- #
# noticias: RSS de Yahoo Finance, sin clave ni cuota
# --------------------------------------------------------------------------- #

def noticias(ticker: str, limite: int = 8):
    url = (f"https://feeds.finance.yahoo.com/rss/2.0/headline"
           f"?s={ticker}&region=US&lang=en-US")
    try:
        raiz = ET.fromstring(_get(url, timeout=20))
    except Exception as e:  # noqa: BLE001
        print(f"   ! RSS {ticker}: {e}", file=sys.stderr)
        return []

    salida = []
    for it in raiz.iterfind(".//item"):
        titulo = (it.findtext("title") or "").strip()
        enlace = (it.findtext("link") or "").strip()
        fecha = (it.findtext("pubDate") or "").strip()
        if titulo and enlace:
            salida.append({"titulo": titulo, "url": enlace,
                           "medio": "Yahoo Finance", "fecha": fecha[5:22]})
    return salida[:limite]


# --------------------------------------------------------------------------- #
# orquestación
# --------------------------------------------------------------------------- #

def leer(ticker: str) -> dict:
    ruta = DIR_DATOS / f"{ticker}.json"
    if ruta.exists():
        try:
            return json.loads(ruta.read_text("utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def procesar(ticker: str, completo: bool) -> dict:
    print(f"-> {ticker} ({'completo' if completo else 'precios'})", flush=True)
    previo = leer(ticker)
    avisos = []

    precios, fuente = serie_precios(ticker)
    if not precios:
        precios, fuente = previo.get("precios", []), previo.get("fuentePrecios", "sin datos")
        avisos.append("serie de precios sin actualizar")

    q = cotizacion(ticker)
    if not q.get("precio") and precios:
        q["precio"] = precios[-1]["c"]
        if len(precios) > 1:
            q["cambio"] = precios[-1]["c"] - precios[-2]["c"]
            q["cambioPct"] = q["cambio"] / precios[-2]["c"] * 100
        avisos.append("cotización derivada del último cierre")

    if completo:
        anual = fundamentales(ticker)
        if not anual:
            anual = previo.get("anual", [])
            avisos.append("fundamentales sin actualizar")
        prensa = noticias(ticker) or previo.get("noticias", [])
        corriente = ttm(ticker, q, anual)
    else:
        anual = previo.get("anual", [])
        prensa = previo.get("noticias", [])
        # En modo ligero el precio cambia, así que hay que rehacer los múltiplos.
        corriente = previo.get("ttm") or {}
        cap, dn = num(q.get("capitalizacion")), num(corriente.get("deudaNeta"))
        if cap is not None and dn is not None:
            ve_antes = num(corriente.get("valorEmpresa"))
            ve_ahora = cap + dn
            if ve_antes:
                factor = ve_ahora / ve_antes
                for k in ("evEbitda", "evFcl"):
                    if corriente.get(k):
                        corriente[k] = corriente[k] * factor
            corriente["valorEmpresa"] = ve_ahora
        if q.get("per"):
            corriente["per"] = q["per"]
        if q.get("bpaTTM"):
            corriente["bpa"] = q["bpaTTM"]

    return {
        "ticker": ticker,
        "nombre": NOMBRES[ticker],
        "actualizado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fuentePrecios": fuente,
        "cotizacion": q,
        "precios": precios[-2600:],
        "anual": anual,
        "ttm": corriente,
        "noticias": prensa,
        "avisos": avisos,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--precios", action="store_true",
                    help="solo precio y cotización, sin tocar fundamentales")
    args = ap.parse_args()
    completo = not args.precios

    DIR_DATOS.mkdir(parents=True, exist_ok=True)
    if not API_KEY:
        print("AVISO: FMP_API_KEY vacía. Solo habrá precios de Stooq.", file=sys.stderr)
    else:
        print(f"Plan configurado para {ANOS} ejercicios de histórico.")

    resumen = []
    for t in TICKERS:
        try:
            d = procesar(t, completo)
        except Exception as e:  # noqa: BLE001
            print(f"ERROR en {t}: {e}", file=sys.stderr)
            d = leer(t)
            if not d:
                continue
            d.setdefault("avisos", []).append(f"fallo de descarga: {e}")

        (DIR_DATOS / f"{t}.json").write_text(
            json.dumps(d, ensure_ascii=False, separators=(",", ":")), "utf-8")

        u = (d.get("anual") or [{}])[-1]
        c = d.get("cotizacion") or {}
        m = d.get("ttm") or {}
        resumen.append({
            "ticker": t, "nombre": d.get("nombre"),
            "precio": c.get("precio"), "cambioPct": c.get("cambioPct"),
            "capitalizacion": c.get("capitalizacion"),
            "per": m.get("per") or c.get("per"),
            "evEbitda": m.get("evEbitda"), "evFcl": m.get("evFcl"),
            "bpa": m.get("bpa") or c.get("bpaTTM"),
            "deudaNeta": m.get("deudaNeta") or u.get("deudaNeta"),
            "deudaNetaEbitda": m.get("deudaNetaEbitda"),
            "margenOperativo": m.get("margenOperativo"),
            "ejercicioBase": m.get("ejercicioBase"),
            "avisos": d.get("avisos") or [],
        })

    (DIR_DATOS / "index.json").write_text(
        json.dumps({"actualizado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "modo": "completo" if completo else "precios",
                    "tickers": TICKERS, "resumen": resumen},
                   ensure_ascii=False, indent=1), "utf-8")

    print(f"\nLlamadas correctas: {contador['ok']} · fallidas: {contador['fallo']}")
    con_datos = sum(1 for r in resumen if r["evEbitda"] is not None)
    print(f"Valores con fundamentales: {con_datos}/{len(resumen)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
