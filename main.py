import os
import re
import requests
from typing import Dict, Any, List

from fastapi import FastAPI
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# -------------------------------------------------
# Cargar variables de entorno
# -------------------------------------------------
load_dotenv()

APIFY_TOKEN = os.getenv("APIFY_TOKEN")
ACTOR_ID = os.getenv("APIFY_ACTOR_ID")  # p.ej. "lukass~idealista-scraper"

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------
# Parsing de la consulta en lenguaje natural
# -------------------------------------------------
def parse_query(q: str) -> Dict[str, Any]:
    """
    Interpreta la frase libre y devuelve:
      - ok: bool
      - missing: lista de cosas que faltan
      - location_query: texto de ubicación (ciudad/barrio/CP/calle)
      - city: ciudad principal (para mostrar mensajes)
      - price_max: presupuesto máximo (€)
      - for_rent: True si parece alquiler
      - num_props: nº de viviendas deseadas (por defecto 5)
    """
    q_low = q.lower()
    missing: List[str] = []

    # 1) Nº de pisos: "5 pisos", "3 apartamentos", etc.
    num_props = 5
    m_props = re.search(r"(\d+)\s+(pisos?|apartamentos?|viviendas?|casas?)", q_low)
    if m_props:
        try:
            num_props = int(m_props.group(1))
        except Exception:
            num_props = 5

    # 2) Precio máximo
    price_max = None

    # Formatos tipo "150 mil"
    m_mil = re.search(r"(\d+)\s*mil", q_low)
    if m_mil:
        price_max = int(m_mil.group(1)) * 1000
    else:
        nums = [int(x) for x in re.findall(r"\d+", q_low)]
        if nums:
            big_nums = [n for n in nums if n >= 5000]
            price_max = max(big_nums) if big_nums else nums[-1]

    if price_max is None:
        missing.append("presupuesto máximo (ej. 'por 300000 euros')")

    # 3) Compra o alquiler
    for_rent = any(
        x in q_low
        for x in ["alquiler", "alquilar", "renta", "renting", "alquilarla", "alquilarlos"]
    )

    # 4) Ubicación / ciudad
    location_query = None

    # Intento 1: todo lo que hay después de " en "
    if " en " in q_low:
        after_en = q_low.split(" en ")[-1]
        for cutter in [" por ", " para ", " que ", " y ", "."]:
            if cutter in after_en:
                after_en = after_en.split(cutter)[0]
        location_query = after_en.strip(" ,.")
        if not location_query:
            location_query = None

    # Intento 2: lista de ciudades conocidas
    ciudades = [
        "madrid",
        "barcelona",
        "valencia",
        "malaga",
        "sevilla",
        "bilbao",
        "zaragoza",
        "cordoba",
        "alicante",
        "murcia",
        "granada",
        "vigo",
        "gijon",
        "oviedo",
        "donostia",
        "san sebastian",
    ]
    city = None
    for c in ciudades:
        if c in q_low:
            city = c
            if not location_query:
                location_query = c
            break

    # Intento 3: código postal (5 dígitos)
    if not location_query:
        m_cp = re.search(r"\b(\d{5})\b", q_low)
        if m_cp:
            location_query = m_cp.group(1)

    if not location_query:
        missing.append("ubicación (ciudad, barrio, código postal o calle)")

    if city is None and location_query:
        city = location_query.split(",")[0].split()[0]

    if city is None:
        city = "madrid"  # fallback

    ok = len(missing) == 0

    return {
        "ok": ok,
        "missing": missing,
        "location_query": location_query,
        "city": city,
        "price_max": price_max,
        "for_rent": for_rent,
        "num_props": num_props,
    }

# -------------------------------------------------
# Rutas de UI estática
# -------------------------------------------------
@app.get("/")
def ui():
    return FileResponse("ui.html")

# -------------------------------------------------
# Utilidades de negocio
# -------------------------------------------------
def safe_price(piso: Dict[str, Any]) -> int:
    try:
        return int(piso.get("price"))
    except Exception:
        return -1

def build_intro(info: Dict[str, Any], rango_min: int | None, rango_max: int | None) -> str:
    ciudad = info["city"]
    price_max = info.get("price_max")
    num_props = info.get("num_props", 5)
    for_rent = info.get("for_rent", False)

    tipo_op = "alquiler" if for_rent else "compra"

    linea_presu = (
        f"Presupuesto máximo aproximado: {price_max:,} €"
        if price_max
        else "Presupuesto máximo no indicado con claridad."
    )

    linea_rango = ""
    if rango_min is not None and rango_max is not None:
        linea_rango = (
            f"\nHe filtrado los anuncios para quedarme con los que están entre "
            f"~{rango_min:,} € y ~{rango_max:,} € (entre -30% y +20% de tu presupuesto)."
        )

    intro = (
        "Eres un asesor inmobiliario experto. Explica al usuario qué has entendido de su búsqueda "
        "(ubicación, presupuesto, tipo de operación, nº de pisos) y qué vas a hacer para encontrar las mejores "
        "oportunidades calidad/precio.\n\n"
        "He entendido lo siguiente de tu búsqueda:\n"
        f"• Zona principal: {ciudad}\n"
        f"• Operación: {tipo_op}\n"
        f"• {linea_presu}\n"
        f"• Nº de propiedades a mostrar: TOP {num_props}\n"
        f"{linea_rango}\n\n"
        "Ahora voy a analizar anuncios reales de Idealista y quedarme con las mejores oportunidades calidad/precio para ti."
    )

    return intro

# -------------------------------------------------
# Endpoint principal /buscar
# -------------------------------------------------
@app.get("/buscar")
def buscar(q: str):
    # Comprobación de variables de entorno
    if not APIFY_TOKEN or not ACTOR_ID:
        return JSONResponse(
            status_code=500,
            content={
                "error": "Faltan APIFY_TOKEN o APIFY_ACTOR_ID en las variables de entorno."
            },
        )

    info = parse_query(q)

    if not info["ok"]:
        return JSONResponse(
            status_code=400,
            content={
                "error": "Falta información en la consulta.",
                "missing": info["missing"],
                "hint": (
                    "Ejemplos:\n"
                    "- 'Busca 5 pisos para comprar en Legazpi, Madrid por 300000 euros'\n"
                    "- 'Quiero 3 pisos en código postal 28005 para alquilar por 150 mil'\n"
                ),
            },
        )

    ciudad = info["city"]
    price_max = info["price_max"]
    for_rent = info["for_rent"]
    location_query = info["location_query"]
    num_props = info["num_props"]

    # Rango de precios [-30%, +20%]
    rango_min = rango_max = None
    if price_max:
        rango_min = int(price_max * 0.7)
        rango_max = int(price_max * 1.2)

    # --------- Llamada al actor lukass~idealista-scraper (sincronamente) ----------
    run_input = {
        "district": location_query or ciudad,
        "country": "es",
        "operation": "rent" if for_rent else "sale",
        "propertyType": "homes",
        "maxItems": 150,
        "endPage": 50,
        "proxy": {
            "useApifyProxy": True,
            "apifyProxyGroups": ["RESIDENTIAL"],
        },
        "minSize": "any",
        "maxSize": "any",
        "bedrooms": [],
        "bathrooms": [],
        "homeType": [],
        "condition": [],
        "propertyStatus": [],
        "floorHeights": [],
        "features": [],
    }

    apify_url = (
        f"https://api.apify.com/v2/acts/{ACTOR_ID}/run-sync-get-dataset-items"
        f"?token={APIFY_TOKEN}"
    )

    try:
        resp = requests.post(apify_url, json=run_input, timeout=300)
        resp.raise_for_status()
        items = resp.json()
    except Exception as e:
        return JSONResponse(
            status_code=502,
            content={"error": f"Error llamando a Apify: {repr(e)}"},
        )

    if not isinstance(items, list) or not items:
        return JSONResponse(
            status_code=404,
            content={"error": "No se encontraron pisos para esta búsqueda."},
        )

    # 1) Solo pisos con precio válido
    con_precio = [p for p in items if safe_price(p) > 0]
    if not con_precio:
        return JSONResponse(
            status_code=404,
            content={"error": "No se encontraron pisos con precio válido."},
        )

    # 2) Filtramos por banda de precio [-30%, +20%]
    candidatos = con_precio
    if rango_min is not None and rango_max is not None:
        banda = [p for p in con_precio if rango_min <= safe_price(p) <= rango_max]
        if banda:
            candidatos = banda

    # 3) Orden por precio (más baratos primero)
    candidatos_ordenados = sorted(candidatos, key=safe_price)

    # 4) TOP N
    top = candidatos_ordenados[: max(num_props, 1)]

    propiedades_salida = []
    for i, piso in enumerate(top, start=1):
        precio = safe_price(piso)
        direccion = piso.get("address") or "Dirección no especificada"
        url = piso.get("url") or ""
        fotos = piso.get("photos") or []
        if isinstance(fotos, list) and fotos:
            foto = fotos[0].get("url") or ""
        else:
            foto = ""
        typology = piso.get("typology") or "vivienda"
        title = piso.get("title") or f"{typology.capitalize()} en {direccion}"

        rent_estimate = None
        if not for_rent and precio > 0:
            rent_estimate = int(precio * 0.04 / 12)

        propiedades_salida.append(
            {
                "rank": i,
                "title": title,
                "address": direccion,
                "price": precio,
                "url": url,
                "photo": foto,
                "operation": "Alquiler" if for_rent else "Compra",
                "typology": typology,
                "rent_estimate": rent_estimate,
            }
        )

    precios_top = [p["price"] for p in propiedades_salida if p["price"] > 0]
    meta = {
        "city": ciudad,
        "location_query": location_query,
        "price_max": price_max,
        "price_band_min": rango_min,
        "price_band_max": rango_max,
        "found_min_price": min(precios_top) if precios_top else None,
        "found_max_price": max(precios_top) if precios_top else None,
        "for_rent": for_rent,
        "num_props": num_props,
        "total_scraped": len(items),
        "total_candidates": len(candidatos),
    }

    intro = build_intro(info, rango_min, rango_max)

    return JSONResponse(
        content={
            "intro": intro,
            "properties": propiedades_salida,
            "meta": meta,
        }
    )

# -------------------------------------------------
# Healthcheck para Azure
# -------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "actor_id": ACTOR_ID}
