import os
import re
import time
import json
from typing import Dict, Any, Optional, List

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# -------------------------------------------------
# Cargar variables de entorno
# -------------------------------------------------
load_dotenv()

APIFY_TOKEN = os.getenv("APIFY_TOKEN")
ACTOR_ID = os.getenv("APIFY_ACTOR_ID")  # p.ej. lukass~idealista-scraper

# Archivo local para guardar los prompts personalizados
PROMPTS_FILE = "prompts.json"

DEFAULT_ASSISTANT_PROMPT = (
    "Eres un asesor inmobiliario experto. Explica al usuario de forma clara, honesta y cercana "
    "lo que has entendido de su búsqueda (ubicación, presupuesto, tipo de operación y número de pisos) "
    "y qué vas a hacer para encontrar las mejores oportunidades calidad/precio."
)

DEFAULT_SUMMARY_PROMPT = (
    "Cierra la respuesta con un breve resumen de la zona y del rango de precios, y da 2–3 "
    "consejos accionables para tomar decisión o afinar la búsqueda (por ejemplo: ampliar zona, "
    "ajustar presupuesto o considerar viviendas para reformar)."
)

# -------------------------------------------------
# Utilidades de prompts
# -------------------------------------------------
def load_prompts() -> Dict[str, str]:
    """Carga los prompts desde fichero o devuelve los valores por defecto."""
    if os.path.exists(PROMPTS_FILE):
        try:
            with open(PROMPTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {
                    "assistant_prompt": data.get("assistant_prompt", DEFAULT_ASSISTANT_PROMPT),
                    "summary_prompt": data.get("summary_prompt", DEFAULT_SUMMARY_PROMPT),
                }
        except Exception:
            pass

    return {
        "assistant_prompt": DEFAULT_ASSISTANT_PROMPT,
        "summary_prompt": DEFAULT_SUMMARY_PROMPT,
    }


def save_prompts(assistant_prompt: str, summary_prompt: str) -> None:
    data = {
        "assistant_prompt": assistant_prompt or DEFAULT_ASSISTANT_PROMPT,
        "summary_prompt": summary_prompt or DEFAULT_SUMMARY_PROMPT,
    }
    with open(PROMPTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# -------------------------------------------------
# App FastAPI
# -------------------------------------------------
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
    price_max: Optional[int] = None

    # Formatos tipo "300000 mil" (mal escrito) o "300 mil"
    m_mil = re.search(r"(\d+)\s*mil", q_low)
    if m_mil:
        price_max = int(m_mil.group(1)) * 1000
    else:
        nums = [int(x) for x in re.findall(r"\d+", q_low)]
        if nums:
            # descartamos números muy pequeños (habitaciones, etc.)
            big_nums = [n for n in nums if n >= 5000]
            price_max = max(big_nums) if big_nums else nums[-1]

    if price_max is None:
        missing.append("presupuesto máximo (ej. 'por 300000 euros')")

    # 3) Compra o alquiler
    for_rent = any(
        x in q_low
        for x in ["alquiler", "alquilar", "renta", "renting", "alquil", "arrendar"]
    )

    # 4) Ubicación / ciudad
    location_query: Optional[str] = None

    # Intento 1: todo lo que hay después de " en "
    if " en " in q_low:
        after_en = q_low.split(" en ")[-1]
        for cutter in [" por ", " para ", " que ", " y ", ".", ","]:
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
    city: Optional[str] = None
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
# Helpers de Idealista
# -------------------------------------------------
def get_price(p: dict) -> Optional[float]:
    """
    Devuelve el precio real del piso sin importar el formato, o None si no existe.
    Idealista puede devolver precios en distintos campos.
    """

    # 1) Campo normal numérico
    if "price" in p and isinstance(p["price"], (int, float)):
        try:
            return float(p["price"])
        except Exception:
            pass

    # 2) price como texto tipo "439.000 €"
    if "price" in p and isinstance(p["price"], str):
        numbers = re.findall(r"\d+", p["price"])
        if numbers:
            try:
                return float("".join(numbers))
            except Exception:
                pass

    # 3) priceValue
    if "priceValue" in p:
        try:
            return float(p["priceValue"])
        except Exception:
            pass

    # 4) priceInfo.amount
    try:
        val = p.get("priceInfo", {}).get("amount")
        if val:
            return float(val)
    except Exception:
        pass

    # 5) operationPrice.buy.amount
    try:
        val = p.get("operationPrice", {}).get("buy", {}).get("amount")
        if val:
            return float(val)
    except Exception:
        pass

    # 6) totalPrice
    if "totalPrice" in p:
        try:
            return float(p["totalPrice"])
        except Exception:
            pass

    return None


# -------------------------------------------------
# Rutas de UI estática
# -------------------------------------------------
@app.get("/")
def ui():
    return FileResponse("ui.html")


# -------------------------------------------------
# API de prompts (para el panel de configuración)
# -------------------------------------------------
@app.get("/prompts")
def get_prompts():
    return load_prompts()


@app.post("/prompts")
def update_prompts(body: Dict[str, str]):
    assistant_prompt = body.get("assistant_prompt", "").strip()
    summary_prompt = body.get("summary_prompt", "").strip()
    save_prompts(assistant_prompt, summary_prompt)
    return {"status": "ok"}


# -------------------------------------------------
# Endpoint principal /buscar (JSON para la UI tipo chat)
# -------------------------------------------------
@app.get("/buscar")
def buscar(q: str):
    if not APIFY_TOKEN or not ACTOR_ID:
        raise HTTPException(
            status_code=500,
            detail="Faltan APIFY_TOKEN o APIFY_ACTOR_ID en las variables de entorno.",
        )

    info = parse_query(q)

    if not info["ok"]:
        return {
            "error": "missing_info",
            "missing": info["missing"],
            "examples": [
                "Busca 5 pisos para comprar en Legazpi, Madrid por 300000 euros",
                "Quiero 3 pisos en código postal 28005 para alquilar por 150 mil",
            ],
        }

    ciudad = info["city"]
    precio_max = info["price_max"]
    for_rent = info["for_rent"]
    location_query = info["location_query"] or ciudad
    num_props = info["num_props"]

    # Rango de precios permitido: -30% / +20%
    price_anchor = float(precio_max)
    price_min = price_anchor * 0.70
    price_max_allowed = price_anchor * 1.20

    # --------- Llamada al actor de Apify ----------
    run_input = {
        "district": location_query,
        "country": "es",
        "operation": "rent" if for_rent else "sale",
        "propertyType": "homes",
        "maxItems": max(num_props * 30, 60),  # pedimos muchos y luego filtramos
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

    start_url = f"https://api.apify.com/v2/acts/{ACTOR_ID}/runs?token={APIFY_TOKEN}"

    try:
        run_res = requests.post(start_url, json=run_input, timeout=60)
        run_res.raise_for_status()
        run = run_res.json()
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Error conectando con Apify: {repr(e)}",
        )

    run_id = run.get("data", {}).get("id") or run.get("id")
    if not run_id:
        raise HTTPException(
            status_code=502,
            detail=f"Apify no devolvió run_id. Respuesta: {run}",
        )

    status_url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={APIFY_TOKEN}"
    data: Dict[str, Any] = {}
    estado = "UNKNOWN"

    # Polling del estado (sin enviar mensajes al usuario, solo logs)
    for _ in range(60):
        try:
            status = requests.get(status_url, timeout=30).json()
        except Exception as e:
            raise HTTPException(
                status_code=502,
                detail=f"Error consultando estado en Apify: {repr(e)}",
            )

        data = status.get("data", {})
        estado = data.get("status") or status.get("status") or "UNKNOWN"
        print(f"[Apify] Estado run {run_id}: {estado}")

        if estado in ["SUCCEEDED", "FAILED", "ABORTED", "TIMING_OUT"]:
            break

        time.sleep(1.5)

    if estado != "SUCCEEDED":
        raise HTTPException(
            status_code=502,
            detail=f"La ejecución en Apify ha terminado con estado: {estado}.",
        )

    dataset_id = data.get("defaultDatasetId") or status.get("defaultDatasetId")
    if not dataset_id:
        raise HTTPException(
            status_code=502,
            detail=f"No se encontró dataset_id en la respuesta de Apify: {status}",
        )

    items_url = (
        f"https://api.apify.com/v2/datasets/{dataset_id}/items"
        f"?clean=true&token={APIFY_TOKEN}"
    )
    try:
        items = requests.get(items_url, timeout=60).json()
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Error descargando resultados del dataset: {repr(e)}",
        )

    if not isinstance(items, list) or not items:
        return {
            "intro": "He revisado Idealista pero no he encontrado pisos para esta búsqueda.",
            "properties": [],
            "summary": "Prueba cambiando la zona, el presupuesto o el tipo de operación.",
            "meta": {
                "city": ciudad,
                "location_query": location_query,
                "price_max": precio_max,
                "for_rent": for_rent,
                "num_props": num_props,
                "price_min": price_min,
                "price_max_allowed": price_max_allowed,
            },
        }

    # -------------------------------------------------
    # Filtrado estricto por precio (-30% / +20%)
    # -------------------------------------------------
    valid_items: List[dict] = []
    for p in items:
        precio = get_price(p)
        if precio is None:
            continue

        if precio < price_min or precio > price_max_allowed:
            continue

        valid_items.append(p)

    if not valid_items:
        return {
            "intro": (
                "He analizado los anuncios de Idealista para tu búsqueda, pero no hay pisos "
                f"en {ciudad.capitalize()} dentro del rango de precios entre "
                f"{price_min:,.0f} € y {price_max_allowed:,.0f} €."
            ),
            "properties": [],
            "summary": (
                "Prueba aumentando ligeramente el presupuesto máximo, ampliando la zona buscada "
                "o incluyendo viviendas para reformar. "
                "Puedes ajustar tu frase y vuelvo a buscar por ti."
            ),
            "meta": {
                "city": ciudad,
                "location_query": location_query,
                "price_max": precio_max,
                "for_rent": for_rent,
                "num_props": num_props,
                "price_min": price_min,
                "price_max_allowed": price_max_allowed,
            },
        }

    # Ordenar por precio (menor a mayor)
    valid_items.sort(key=lambda x: get_price(x) or 0.0)

    # TOP N
    top = valid_items[: max(num_props, 1)]

    properties: List[dict] = []
    for i, piso in enumerate(top, start=1):
        precio = get_price(piso)

        direccion = piso.get("address") or "Dirección no especificada"
        url = piso.get("url") or ""
        fotos = piso.get("photos") or []
        if isinstance(fotos, list) and fotos:
            foto = fotos[0].get("url") or ""
        else:
            foto = ""

        typology = piso.get("typology") or "Propiedad"
        title = piso.get("title") or f"{typology.capitalize()} en {direccion}"

        alquiler_estimado: Optional[int] = None
        if not for_rent and precio:
            alquiler_estimado = int(precio * 0.04 / 12)

        properties.append(
            {
                "rank": i,
                "address": direccion,
                "operation": "Alquiler" if for_rent else "Compra",
                "price": precio,
                "url": url,
                "photo": foto,
                "typology": typology,
                "title": title,
                "rent_estimate": alquiler_estimado,
            }
        )

    prompts = load_prompts()
    assistant_prompt = prompts["assistant_prompt"]
    summary_prompt = prompts["summary_prompt"]

    price_max_str = f"{precio_max:,.0f} €"
    intro = (
        f"{assistant_prompt}\n\n"
        f"He entendido lo siguiente de tu búsqueda:\n"
        f"- Zona principal: {ciudad.capitalize()}\n"
        f"- Operación: {'alquiler' if for_rent else 'compra'}\n"
        f"- Presupuesto máximo aproximado: {price_max_str}\n"
        f"- Nº de propiedades a mostrar: TOP {num_props}\n\n"
        "Ahora voy a analizar anuncios reales de Idealista y quedarme con las mejores "
        "oportunidades calidad/precio para ti."
    )

    summary = (
        f"En resumen, he encontrado {len(properties)} opciones en la zona de "
        f"{ciudad.capitalize()} dentro del rango de precios entre "
        f"{price_min:,.0f} € y {price_max_allowed:,.0f} €.\n\n"
        f"{summary_prompt}"
    )

    return {
        "intro": intro,
        "properties": properties,
        "summary": summary,
        "meta": {
            "city": ciudad,
            "location_query": location_query,
            "price_max": precio_max,
            "for_rent": for_rent,
            "num_props": num_props,
            "price_min": price_min,
            "price_max_allowed": price_max_allowed,
        },
    }


# -------------------------------------------------
# Healthcheck para Azure
# -------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "actor_id": ACTOR_ID}
