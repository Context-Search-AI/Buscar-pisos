import os
import re
import time
import requests
from typing import Dict, Any

from fastapi import FastAPI
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# -------------------------------------------------
# Cargar variables de entorno
# -------------------------------------------------
load_dotenv()

APIFY_TOKEN = os.getenv("APIFY_TOKEN")
ACTOR_ID = os.getenv("APIFY_ACTOR_ID")  # debe ser lukass~idealista-scraper

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
      - price_max: presupuesto máximo (€) SOLO para mostrar
      - for_rent: True si parece alquiler
      - num_props: nº de viviendas deseadas (por defecto 5)
    """
    q_low = q.lower()
    missing = []

    # 1) Nº de pisos: "5 pisos", "3 apartamentos", etc.
    num_props = 5
    m_props = re.search(r"(\d+)\s+(pisos?|apartamentos?|viviendas?|casas?)", q_low)
    if m_props:
        try:
            num_props = int(m_props.group(1))
        except Exception:
            num_props = 5

    # 2) Precio máximo (no lo filtra el actor, pero lo mostramos)
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
# Endpoint principal /buscar (streaming)
# -------------------------------------------------
@app.get("/buscar")
def buscar(q: str):
    def generate():
        # Comprobación de variables de entorno
        if not APIFY_TOKEN or not ACTOR_ID:
            yield "⚠️ Falta APIFY_TOKEN o APIFY_ACTOR_ID en las variables de entorno.\n"
            yield "   Configúralas en Azure → Configuration antes de seguir.\n"
            return

        info = parse_query(q)

        if not info["ok"]:
            yield "⚠️ Me falta información para poder buscar bien:\n"
            for item in info["missing"]:
                yield f"   • {item}\n"
            yield (
                "\nPor ejemplo:\n"
                "  - 'Busca 5 pisos para comprar en Legazpi, Madrid por 300000 euros'\n"
                "  - 'Quiero 3 pisos en código postal 28005 para alquilar por 150 mil'\n"
            )
            return

        ciudad = info["city"]
        precio_max = info["price_max"]
        for_rent = info["for_rent"]
        location_query = info["location_query"]
        num_props = info["num_props"]

        yield f"🔍 Consulta: {q}\n"
        yield f"📍 Ubicación detectada: {location_query or ciudad}\n"
        yield f"💶 Precio máximo (orientativo): {precio_max} €\n"
        yield f"🏷 Tipo: {'Alquiler' if for_rent else 'Compra'}\n"
        yield f"📦 Nº de propiedades a buscar (TOP): {num_props}\n\n"
        yield "⏳ Lanzando búsqueda en Apify…\n"

        # --------- AQUÍ adaptamos al actor lukass~idealista-scraper ----------
        run_input = {
            "district": location_query or ciudad,
            "country": "es",
            "operation": "rent" if for_rent else "sale",
            "propertyType": "homes",
            "maxItems": max(num_props * 10, 20),  # rascamos de más y luego ordenamos
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
        # -------------------------------------------------------------------

        start_url = f"https://api.apify.com/v2/acts/{ACTOR_ID}/runs?token={APIFY_TOKEN}"

        try:
            run_res = requests.post(start_url, json=run_input, timeout=60)
            run_res.raise_for_status()
            run = run_res.json()
        except Exception as e:
            yield f"❌ Error conectando con Apify: {repr(e)}\n"
            return

        run_id = run.get("data", {}).get("id") or run.get("id")
        if not run_id:
            yield f"❌ Apify no devolvió run_id. Respuesta: {run}\n"
            return

        status_url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={APIFY_TOKEN}"
        data = {}
        estado = "UNKNOWN"

        for _ in range(60):
            try:
                status = requests.get(status_url, timeout=30).json()
            except Exception as e:
                yield f"❌ Error consultando estado en Apify: {repr(e)}\n"
                return

            data = status.get("data", {})
            estado = data.get("status") or status.get("status") or "UNKNOWN"
            yield f"⏳ Buscando pisos en {location_query or ciudad.capitalize()}… Estado: {estado}\n"

            if estado in ["SUCCEEDED", "FAILED", "ABORTED", "TIMING_OUT"]:
                break

            time.sleep(1.5)

        if estado != "SUCCEEDED":
            yield f"❌ La ejecución en Apify ha terminado con estado: {estado}.\n"
            return

        dataset_id = data.get("defaultDatasetId") or status.get("defaultDatasetId")
        if not dataset_id:
            yield f"❌ No se encontró dataset_id en la respuesta de Apify: {status}\n"
            return

        items_url = (
            f"https://api.apify.com/v2/datasets/{dataset_id}/items"
            f"?clean=true&token={APIFY_TOKEN}"
        )
        try:
            items = requests.get(items_url, timeout=60).json()
        except Exception as e:
            yield f"❌ Error descargando resultados del dataset: {repr(e)}\n"
            return

        if not isinstance(items, list) or not items:
            yield "⚠️ No se encontraron pisos para esta búsqueda.\n"
            return

        # 4) TOP N por precio (campo 'price')
        def extraer_precio(p):
            try:
                return float(p.get("price"))
            except Exception:
                return 9_999_999_999

        items_ordenados = sorted(items, key=extraer_precio)
        top = items_ordenados[: max(num_props, 1)]

        yield "\n🏡 TOP propiedades encontradas:\n"

        for i, piso in enumerate(top, start=1):
            precio = extraer_precio(piso)

            direccion = piso.get("address") or "Dirección no especificada"
            url = piso.get("url") or "Sin enlace"

            fotos = piso.get("photos") or []
            if isinstance(fotos, list) and fotos:
                foto = fotos[0].get("url") or "Sin foto disponible"
            else:
                foto = "Sin foto disponible"

            area = piso.get("typology") or ciudad.capitalize()
            descripcion_corta = piso.get("title") or "Sin descripción detallada."

            # Estimación de alquiler si es compra
            alquiler_estimado = None
            if not for_rent and precio and precio < 9_000_000_000:
                alquiler_estimado = int(precio * 0.04 / 12)

            tipo_operacion = "Alquiler" if for_rent else "Compra"

            yield f"\n{i}. Propiedad\n"
            yield f"   📍 Dirección: {direccion}\n"
            yield f"   🏷 Operación: {tipo_operacion}\n"
            yield f"   💶 Total: {precio:,.0f} €\n"
            yield f"   🔗 Link: {url}\n"
            yield f"   🖼 Foto: {foto}\n"
            yield f"   📌 Tipo: {area}\n"
            yield f"   📝 Resumen: {descripcion_corta}\n"

            if alquiler_estimado is not None:
                yield (
                    f"   📊 Estimación alquiler: ~{alquiler_estimado:,.0f} €/mes "
                    f"(supuesto 4% rentabilidad bruta anual)\n"
                )

    return StreamingResponse(generate(), media_type="text/plain; charset=utf-8")


# -------------------------------------------------
# Healthcheck para Azure
# -------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "actor_id": ACTOR_ID}
