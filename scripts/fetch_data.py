#!/usr/bin/env python3
"""
Métricas de GOOGL, MSFT, META y AMZN.

Principio de diseño
-------------------
No se usa NINGÚN ratio precalculado del proveedor. FMP se emplea solo como fuente
de cifras en bruto (ingresos, EBITDA, beneficio, capex, flujo operativo, deuda,
caja, acciones, capitalización) y todos los múltiplos se derivan aquí con una
única definición:

    caja           = efectivo + inversiones a corto plazo
    deuda neta     = deuda total - caja           (negativa = caja neta)
    valor empresa  = capitalización + deuda neta
    PER            = capitalización / beneficio
    EV/EBITDA      = valor empresa / EBITDA
    EV/FCF         = valor empresa / (flujo operativo - capex)

Los datos TTM se obtienen sumando los cuatro últimos trimestres publicados, no
de un endpoint aparte. Así el múltiplo de hoy y el rango histórico son
directamente comparables, que es lo que hacía falta para las bandas.

Uso
---
    python scripts/fetch_data.py            # completo    (9 llamadas por valor)
    python scripts/fetch_data.py --precios  # solo precio (2 llamadas por valor)
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
# HTTP
# --------------------------------------------------------------------------- #

def _get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "megacap-tracker/3.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fmp(ruta: str, ruta_legacy: str = "", **params):
    if not API_KEY:
        return None
    simbolo = params.get("symbol", "")
    urls = [f"{BASE}/{ruta}?" + urllib.parse.urlencode({**params, "apikey": API_KEY})]
    if ruta_legacy and simbolo:
        resto = {k: v for k, v in params.items() if k != "symbol"}
        urls.append(f"{BASE_LEGACY}/{ruta_legacy}/{simbolo}?"
                    + urllib.parse.urlencode({**resto, "apikey": API_KEY}))

    for url in urls:
        publica = url.split("apikey=")[0].rstrip("?&")
        motivo = "sin respuesta"
        try:
            datos = json.loads(_get(url))
            if isinstance(datos, dict) and (datos.get("Error Message") or datos.get("error")):
                motivo = str(datos.get("Error Message") or datos.get("error"))[:150]
            elif datos:
                contador["ok"] += 1
                return datos
            else:
                motivo = "respuesta vacia"
        except urllib.error.HTTPError as e:
            try:
                motivo = f"HTTP {e.code} {e.read().decode('utf-8', 'replace')[:150]}"
            except Exception:
                motivo = f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001
            motivo = f"{type(e).__name__}: {e}"
        finally:
            time.sleep(PAUSA)
        print(f"   ! {publica} -> {motivo}", file=sys.stderr)

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
    """Division defensiva: None si el denominador es nulo, cero o negativo."""
    a, b = num(a), num(b)
    return a / b if (a is not None and b is not None and b > 0) else None


def suma(valores):
    v = [num(x) for x in valores]
    return sum(x for x in v if x is not None) if any(x is not None for x in v) else None


# --------------------------------------------------------------------------- #
# extraccion de cifras en bruto
# --------------------------------------------------------------------------- #

def _resultados(p: dict) -> dict:
    return {
        "ingresos": num(campo(p, "revenue")),
        "ebitda": num(campo(p, "ebitda", "EBITDA")),
        "resultadoOperativo": num(campo(p, "operatingIncome")),
        "beneficio": num(campo(p, "netIncome")),
        "bpa": num(campo(p, "epsDiluted", "epsdiluted", "eps")),
        "acciones": num(campo(p, "weightedAverageShsOutDil", "weightedAverageShsOut")),
    }


def _balance(b: dict) -> dict:
    """Caja = efectivo + inversiones a corto. Sin esto estas companias parecen endeudadas."""
    deuda = num(campo(b, "totalDebt"))
    efectivo = num(campo(b, "cashAndCashEquivalents"))
    corto = num(campo(b, "shortTermInvestments"))
    caja = num(campo(b, "cashAndShortTermInvestments"))
    if caja is None and efectivo is not None:
        caja = efectivo + (corto or 0)
    return {
        "deudaTotal": deuda,
        "caja": caja,
        "deudaNeta": (deuda - caja) if (deuda is not None and caja is not None) else None,
        "patrimonio": num(campo(b, "totalStockholdersEquity", "totalEquity")),
    }


def _caja(c: dict) -> dict:
    fco = num(campo(c, "operatingCashFlow", "netCashProvidedByOperatingActivities"))
    capex = num(campo(c, "capitalExpenditure"))
    fcl = num(campo(c, "freeCashFlow"))
    if fcl is None and fco is not None and capex is not None:
        fcl = fco + capex  # el capex llega con signo negativo
    return {"flujoOperativo": fco, "capex": capex, "fcl": fcl}


def _multiplos(capitalizacion, deuda_neta, beneficio, ebitda, fcl) -> dict:
    ve = ((capitalizacion + deuda_neta)
          if (capitalizacion is not None and deuda_neta is not None) else None)
    positivo = ve is not None and ve > 0
    return {
        "valorEmpresa": ve,
        "per": div(capitalizacion, beneficio),
        "evEbitda": div(ve, ebitda) if positivo else None,
        "evFcl": div(ve, fcl) if positivo else None,
        "deudaNetaEbitda": (div(deuda_neta, ebitda)
                            if (deuda_neta is not None and deuda_neta > 0) else None),
    }


# --------------------------------------------------------------------------- #
# precios
# --------------------------------------------------------------------------- #

def precios_stooq(ticker: str):
    try:
        t = _get(f"https://stooq.com/q/d/l/?s={ticker.lower()}.us&i=d").decode("utf-8", "replace")
    except Exception:
        return []
    out = []
    for f in csv.DictReader(io.StringIO(t)):
        c = num(f.get("Close"))
        if f.get("Date") and c:
            out.append({"f": f["Date"], "c": round(c, 4), "v": num(f.get("Volume")) or 0})
    return out


def serie_precios(ticker: str):
    d = fmp("historical-price-eod/light", "historical-price-full", symbol=ticker)
    filas = d.get("historical") if isinstance(d, dict) else d
    if filas:
        out = []
        for f in filas:
            c = num(campo(f, "close", "adjClose", "price"))
            fe = campo(f, "date")
            if fe and c:
                out.append({"f": str(fe)[:10], "c": round(c, 4), "v": num(campo(f, "volume")) or 0})
        out.sort(key=lambda x: x["f"])
        if out:
            return out, "FMP"
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
    }


# --------------------------------------------------------------------------- #
# serie anual
# --------------------------------------------------------------------------- #

def serie_anual(ticker: str):
    km = fmp("key-metrics", "key-metrics", symbol=ticker, period="annual", limit=ANOS) or []
    pg = fmp("income-statement", "income-statement", symbol=ticker, period="annual", limit=ANOS) or []
    bl = fmp("balance-sheet-statement", "balance-sheet-statement", symbol=ticker, period="annual", limit=ANOS) or []
    cf = fmp("cash-flow-statement", "cash-flow-statement", symbol=ticker, period="annual", limit=ANOS) or []

    def idx(l):
        return {str(campo(x, "date", defecto=""))[:10]: x for x in l if isinstance(x, dict)}

    km_i, pg_i, bl_i, cf_i = idx(km), idx(pg), idx(bl), idx(cf)
    serie = []

    for f in sorted(set().union(km_i, pg_i, bl_i, cf_i)):
        k = km_i.get(f, {})
        r = _resultados(pg_i.get(f, {}))
        b = _balance(bl_i.get(f, {}))
        c = _caja(cf_i.get(f, {}))

        # Capitalizacion al cierre del ejercicio: unica cifra de mercado tomada del
        # proveedor, porque no se puede reconstruir desde las cuentas.
        capitalizacion = num(campo(k, "marketCap"))
        m = _multiplos(capitalizacion, b["deudaNeta"], r["beneficio"], r["ebitda"], c["fcl"])

        serie.append({
            "f": f,
            "ejercicio": str(campo(k, "fiscalYear")
                             or campo(pg_i.get(f, {}), "fiscalYear", "calendarYear")
                             or f[:4]),
            **r, **b, **c, **m,
            "capitalizacion": capitalizacion,
            "margenOperativo": div(r["resultadoOperativo"], r["ingresos"]),
            "margenNeto": div(r["beneficio"], r["ingresos"]),
            "margenFcl": div(c["fcl"], r["ingresos"]),
            "roic": num(campo(k, "returnOnInvestedCapital")),
            "roe": num(campo(k, "returnOnEquity")) or div(r["beneficio"], b["patrimonio"]),
        })

    for i, x in enumerate(serie):
        prev = serie[i - 1]["ingresos"] if i else None
        x["crecimientoIngresos"] = (x["ingresos"] / prev - 1) if (prev and x["ingresos"]) else None
    return serie


# --------------------------------------------------------------------------- #
# TTM por suma de los cuatro ultimos trimestres
# --------------------------------------------------------------------------- #

def serie_ttm(ticker: str, q: dict, anual: list) -> dict:
    pg = fmp("income-statement", "income-statement", symbol=ticker, period="quarter", limit=5) or []
    bl = fmp("balance-sheet-statement", "balance-sheet-statement", symbol=ticker, period="quarter", limit=2) or []
    cf = fmp("cash-flow-statement", "cash-flow-statement", symbol=ticker, period="quarter", limit=5) or []

    def ordenar(l):
        return sorted([x for x in l if isinstance(x, dict)],
                      key=lambda x: str(campo(x, "date", defecto="")))

    pg, bl, cf = ordenar(pg), ordenar(bl), ordenar(cf)
    u = anual[-1] if anual else {}
    capitalizacion = num(q.get("capitalizacion"))

    # Sin cuatro trimestres no hay TTM: se usa el ejercicio cerrado y se avisa,
    # en vez de publicar una cifra a medias que parezca actual.
    if len(pg) < 4 or len(cf) < 4:
        b = _balance(bl[-1]) if bl else {
            "deudaTotal": u.get("deudaTotal"), "caja": u.get("caja"),
            "deudaNeta": u.get("deudaNeta"), "patrimonio": None}
        m = _multiplos(capitalizacion, b["deudaNeta"], u.get("beneficio"),
                       u.get("ebitda"), u.get("fcl"))
        return {
            "base": f"ejercicio {u.get('ejercicio', '-')} (sin trimestres para TTM)",
            "completo": False,
            "cierre": u.get("f"),
            **{k: u.get(k) for k in ("ingresos", "ebitda", "beneficio", "bpa", "acciones",
                                     "fcl", "capex", "margenOperativo", "margenNeto",
                                     "margenFcl", "crecimientoIngresos", "roic")},
            **b, **m,
        }

    p4, c4 = pg[-4:], cf[-4:]
    ingresos = suma(x.get("revenue") for x in p4)
    ebitda = suma(campo(x, "ebitda", "EBITDA") for x in p4)
    operativo = suma(x.get("operatingIncome") for x in p4)
    beneficio = suma(x.get("netIncome") for x in p4)

    fco = suma(campo(x, "operatingCashFlow", "netCashProvidedByOperatingActivities") for x in c4)
    capex = suma(x.get("capitalExpenditure") for x in c4)
    fcl = (fco + capex) if (fco is not None and capex is not None) else None

    acciones = num(campo(p4[-1], "weightedAverageShsOutDil", "weightedAverageShsOut"))
    bpa = div(beneficio, acciones)

    b = _balance(bl[-1]) if bl else {
        "deudaTotal": None, "caja": None, "deudaNeta": u.get("deudaNeta"), "patrimonio": None}
    m = _multiplos(capitalizacion, b["deudaNeta"], beneficio, ebitda, fcl)

    return {
        "base": "suma de los cuatro ultimos trimestres",
        "completo": True,
        "cierre": str(campo(p4[-1], "date", defecto=""))[:10],
        "ingresos": ingresos, "ebitda": ebitda, "beneficio": beneficio,
        "bpa": bpa, "acciones": acciones, "fcl": fcl, "capex": capex,
        "margenOperativo": div(operativo, ingresos),
        "margenNeto": div(beneficio, ingresos),
        "margenFcl": div(fcl, ingresos),
        "crecimientoIngresos": num(u.get("crecimientoIngresos")),
        "roic": num(u.get("roic")),
        **b, **m,
    }


# --------------------------------------------------------------------------- #
# noticias
# --------------------------------------------------------------------------- #

def noticias(ticker: str, limite: int = 8):
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
    try:
        raiz = ET.fromstring(_get(url, timeout=20))
    except Exception as e:  # noqa: BLE001
        print(f"   ! RSS {ticker}: {e}", file=sys.stderr)
        return []
    out = []
    for it in raiz.iterfind(".//item"):
        t = (it.findtext("title") or "").strip()
        enlace = (it.findtext("link") or "").strip()
        f = (it.findtext("pubDate") or "").strip()
        if t and enlace:
            out.append({"titulo": t, "url": enlace, "medio": "Yahoo Finance", "fecha": f[5:22]})
    return out[:limite]


# --------------------------------------------------------------------------- #
# orquestacion
# --------------------------------------------------------------------------- #

def leer(ticker: str) -> dict:
    r = DIR_DATOS / f"{ticker}.json"
    if r.exists():
        try:
            return json.loads(r.read_text("utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def reescalar(ttm: dict, capitalizacion) -> dict:
    """En modo ligero cambia el precio: se rehacen los multiplos sin pedir cuentas."""
    if not ttm or capitalizacion is None:
        return ttm
    m = _multiplos(capitalizacion, num(ttm.get("deudaNeta")), ttm.get("beneficio"),
                   ttm.get("ebitda"), ttm.get("fcl"))
    return {**ttm, **m}


def procesar(ticker: str, completo: bool) -> dict:
    print(f"-> {ticker} ({'completo' if completo else 'precios'})", flush=True)
    previo, avisos = leer(ticker), []

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
        avisos.append("cotizacion derivada del ultimo cierre")

    if completo:
        anual = serie_anual(ticker)
        if not anual:
            anual = previo.get("anual", [])
            avisos.append("fundamentales sin actualizar")
        ttm = serie_ttm(ticker, q, anual)
        if not ttm.get("completo"):
            avisos.append("TTM incompleto: se muestra el ultimo ejercicio cerrado")
        prensa = noticias(ticker) or previo.get("noticias", [])
    else:
        anual = previo.get("anual", [])
        ttm = reescalar(previo.get("ttm") or {}, q.get("capitalizacion"))
        prensa = previo.get("noticias", [])

    return {
        "ticker": ticker, "nombre": NOMBRES[ticker],
        "actualizado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fuentePrecios": fuente, "cotizacion": q,
        "precios": precios[-2600:], "anual": anual, "ttm": ttm,
        "noticias": prensa, "avisos": avisos,
    }


def _fmt(v, d=1):
    return "-" if v is None else f"{v:.{d}f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--precios", action="store_true")
    completo = not ap.parse_args().precios

    DIR_DATOS.mkdir(parents=True, exist_ok=True)
    if not API_KEY:
        print("AVISO: FMP_API_KEY vacia. Solo habra precios de Stooq.", file=sys.stderr)

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

        m = d.get("ttm") or {}
        c = d.get("cotizacion") or {}
        resumen.append({
            "ticker": t, "nombre": d.get("nombre"),
            "precio": c.get("precio"), "cambioPct": c.get("cambioPct"),
            "capitalizacion": c.get("capitalizacion"),
            "per": m.get("per"), "evEbitda": m.get("evEbitda"), "evFcl": m.get("evFcl"),
            "bpa": m.get("bpa"), "deudaNeta": m.get("deudaNeta"),
            "deudaNetaEbitda": m.get("deudaNetaEbitda"),
            "margenOperativo": m.get("margenOperativo"),
            "base": m.get("base"), "avisos": d.get("avisos") or [],
        })

        # Control de coherencia en el propio registro, para poder auditarlo a simple vista.
        print(f"   BPA {_fmt(m.get('bpa'), 2):>7}  PER {_fmt(m.get('per')):>6}  "
              f"EV/EBITDA {_fmt(m.get('evEbitda')):>6}  EV/FCF {_fmt(m.get('evFcl')):>7}  "
              f"[{m.get('base')}]")

    (DIR_DATOS / "index.json").write_text(
        json.dumps({"actualizado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "modo": "completo" if completo else "precios",
                    "tickers": TICKERS, "resumen": resumen},
                   ensure_ascii=False, indent=1), "utf-8")

    print(f"\nLlamadas correctas: {contador['ok']} - fallidas: {contador['fallo']}")
    print(f"Valores con multiplos: {sum(1 for r in resumen if r['evEbitda'])}/{len(resumen)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
