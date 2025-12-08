import os
import re
import time
import requests
from fastapi import FastAPI
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Cargar variables de entorno desde .env (si existe)
load_dotenv()

APIFY_TOKEN = os.getenv("APIFY_TOKEN")
ACTOR_ID = os.getenv("APIFY_ACTOR_ID")  # p.ej. "igylo/idealista-scraper"

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------
# Utilidad: interpretar la query libre
# -----------------------------
def parse_query(q: str):
    q_low = q.lower()

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
    ]
    ciudad = next((c for c in ciudades if c in q_low), "madrid")

    nums = re.findall(r"\d+", q_low)
    precio_max = int(nums[-1]) if nums else 250000

    for_rent = any(x in q_low for x in ["alquiler", "alquilar", "rent", "alquilarla"])

    return ciudad, precio_max, for_rent


# -----------------------------
# Rutas para servir UI estática
# -----------------------------
@app.get("/")
def ui():
    return FileResponse("ui.html")


@app.get("/main.js")
def main_js():
    # MUY IMPORTANTE: sirve el JS que necesita el navegador
    return FileResponse("main.js")


# -----------------------------
# /buscar → usa Apify y hace streaming de texto
# -----------------------------
@app.get("/buscar")
def buscar(q: str):

    def generate():
        if not APIFY_TOKEN or not ACTOR_ID:
            yield "⚠️ Falta APIFY_TOKEN o APIFY_ACTOR_ID en las variables de entorno.\n"
            return

        ciudad, precio_max, for_rent = parse_query(q)

        yield f"🔍 Consulta: {q}\n"
        yield f"📍 Ciudad detectada: {ciudad.capitalize()}\n"
        yield f"💶 Precio máximo: {precio_max} €\n"
        yield f"🏷 Tipo: {'Alquiler' if for_rent else 'Compra'}\n\n"
        yield "⏳ Lanzando búsqueda en Apify…\n"

        run_input = {
            "ciudad": ciudad,
            "precio_max": precio_max,
            "for_rent": for_rent,
        }

        # 1) Lanzar actor
        start_url = f"https://api.apify.com/v2/acts/{ACTOR_ID}/runs?token={APIFY_TOKEN}"
        try:
            run_res = requests.post(start_url, json=run_input, timeout=30)
            run = run_res.json()
        except Exception as e:
            yield f"❌ Error conectando con Apify: {repr(e)}\n"
            return

        run_id = run.get("id") or run.get("data", {}).get("id")
        if not run_id:
            yield f"❌ Apify no devolvió run_id. Respuesta: {run}\n"
            return

        # 2) Polling de estado
        status_url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={APIFY_TOKEN}"
        estado = "UNKNOWN"
        data = {}

        for _ in range(60):  # máx ~60 segundos
            try:
                status = requests.get(status_url, timeout=15).json()
            except Exception as e:
                yield f"❌ Error consultando estado en Apify: {repr(e)}\n"
                return

            data = status.get("data", {})
            estado = data.get("status") or status.get("status") or "UNKNOWN"

            yield f"⏳ Buscando pisos en {ciudad.capitalize()}… Estado: {estado}\n"
            if estado in ["SUCCEEDED", "FAILED"]:
                break

            time.sleep(1.5)

        if estado == "FAILED":
            yield "❌ La ejecución en Apify ha fallado.\n"
            return

        dataset_id = data.get("defaultDatasetId") or status.get("defaultDatasetId")
        if not dataset_id:
            yield f"❌ No se encontró dataset_id en la respuesta de Apify: {status}\n"
            return

        # 3) Obtener items del dataset
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

        # 4) TOP 5 por precio
        def extraer_precio(p):
            try:
                if "precio" in p:
                    return float(p["precio"])
                if "priceInfo" in p:
                    return float(p["priceInfo"]["price"]["amount"])
            except Exception:
                pass
            return 9_999_999_999

        top5 = sorted(items, key=extraer_precio)[:5]

        yield "\n🏡 TOP 5 propiedades encontradas:\n"

        for i, piso in enumerate(top5, start=1):
            precio = extraer_precio(piso)
            titulo = piso.get("title") or piso.get("tipo") or "Propiedad"
            zona = (
                piso.get("zona")
                or piso.get("neighborhood")
                or piso.get("address")
                or "Zona no especificada"
            )
            url = piso.get("url") or piso.get("link") or "Sin enlace"

            motivo = (
                "Motivo TOP: precio competitivo para la zona, "
                "potencial de demanda y buenas características."
            )

            yield (
                f"\n{i}. {titulo}\n"
                f"   📍 {zona}\n"
                f"   💶 {precio:,.0f} €\n"
                f"   🔗 {url}\n"
                f"   💡 {motivo}\n"
            )

    return StreamingResponse(generate(), media_type="text/plain; charset=utf-8")


# -----------------------------
# Healthcheck para Azure
# -----------------------------
@app.get("/health")
def health():
    return {"status": "ok"}
