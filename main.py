import os
import json
from typing import List, Optional

import requests
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from dotenv import load_dotenv
import openai

# --------------------------
# CARGA DE VARIABLES
# --------------------------
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN")
APIFY_ACTOR_ID = os.getenv("APIFY_ACTOR_ID", "igolaizola/idealista-scraper")

if not OPENAI_API_KEY:
    raise RuntimeError("Falta OPENAI_API_KEY en variables de entorno")

openai.api_key = OPENAI_API_KEY


# --------------------------
# MODELOS Pydantic
# --------------------------

class PropertyInput(BaseModel):
    titulo: Optional[str] = None
    precio: Optional[float] = None
    alquiler_estimado: Optional[float] = None
    url: Optional[str] = None
    ciudad: Optional[str] = None
    habitaciones: Optional[int] = None
    banos: Optional[int] = None
    m2: Optional[float] = None


class PropertyEnriched(PropertyInput):
    rentabilidad_bruta: float
    rentabilidad_neta: float
    hipoteca_mensual: float
    cashflow_anual: float


class AnalysisResponse(BaseModel):
    query: str
    ciudad: str
    total_analizados: int
    top5: List[dict]
    raw: List[PropertyEnriched]


# --------------------------
# APP FASTAPI
# --------------------------

app = FastAPI(
    title="Inmobiliario GPT API",
    description="API para analizar pisos, calcular rentabilidad y seleccionar TOP 5 anuncios.",
    version="1.0.0",
)

# CORS CORRECTO (sin error de 'app' faltante)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],     # ajusta en producción
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------
# FUNCIONES FINANCIERAS
# --------------------------

def mortgage_payment(principal: float, annual_rate: float, years: int) -> float:
    """Cuota mensual de hipoteca francesa."""
    if principal <= 0 or years <= 0:
        return 0.0
    r = annual_rate / 12.0
    n = years * 12
    if r == 0:
        return principal / n
    return float(principal * (r * (1 + r) ** n) / ((1 + r) ** n - 1))


def calculate_yields(price: float, rent_monthly: float, reforma: float, gastos_anuales: float = 1200.0) -> dict:
    """Rentabilidad bruta y neta aproximada."""
    if price <= 0:
        return {"rent_bruta": 0.0, "rent_neta": 0.0}
    rent_anual = rent_monthly * 12.0
    amortizacion_reforma = reforma / 10.0 if reforma > 0 else 0.0
    rentabilidad_bruta = rent_anual / price
    rentabilidad_neta = (rent_anual - gastos_anuales - amortizacion_reforma) / price
    return {
        "rent_bruta": round(rentabilidad_bruta * 100, 2),
        "rent_neta": round(rentabilidad_neta * 100, 2),
    }


# --------------------------
# APIFY: SCRAPING PORTALES
# --------------------------

def fetch_properties_from_apify(
    ciudad: str,
    max_price: int,
    limit: int = 20,
    for_rent: bool = True,
) -> list:
    """Llama al actor de Apify (Idealista/Fotocasa) y devuelve lista de propiedades."""
    if not APIFY_API_TOKEN:
        raise RuntimeError("Falta APIFY_API_TOKEN en variables de entorno")

    url = f"https://api.apify.com/v2/acts/{APIFY_ACTOR_ID}/run-sync-get-dataset-items?token={APIFY_API_TOKEN}"

    payload = {
        "location": ciudad,
        "operation": "rent" if for_rent else "sale",
        "maxItems": limit,
        "maxPrice": max_price,
    }

    try:
        resp = requests.post(url, json=payload, timeout=120)
        resp.raise_for_status()
        items = resp.json()
        if not isinstance(items, list):
            return []
        return items
    except Exception as e:
        print("Error llamando a Apify:", e)
        return []


def map_raw_item_to_property(item: dict, ciudad: str) -> PropertyInput:
    """Mapea un item crudo de Apify a nuestro modelo estándar."""
    precio = item.get("price") or item.get("priceValue") or 0
    alquiler_estimado = item.get("rentPrice") or item.get("rentEstimate") or 600
    titulo = item.get("title") or item.get("headline") or "Piso sin título"
    url = item.get("url") or item.get("detailUrl")
    habitaciones = item.get("rooms") or item.get("bedrooms") or None
    banos = item.get("bathrooms") or None
    m2 = item.get("size") or item.get("floorArea") or None

    return PropertyInput(
        titulo=titulo,
        precio=float(precio) if precio else 0.0,
        alquiler_estimado=float(alquiler_estimado) if alquiler_estimado else 0.0,
        url=url,
        ciudad=ciudad,
        habitaciones=habitaciones,
        banos=banos,
        m2=float(m2) if m2 else None,
    )


# --------------------------
# RANKING CON OPENAI (SDK 0.28.1)
# --------------------------

def rank_properties_with_ai(user_query: str, pisos: list) -> list:
    prompt = f"""
Eres un analista inmobiliario experto en inversión (compra para alquilar) en España.

El usuario ha pedido: "{user_query}"

Tienes la siguiente lista de pisos ya calculados (rentabilidades, hipoteca, cashflow):

{json.dumps(pisos, ensure_ascii=False, indent=2)}

Tu tarea:
1. Analizar estos pisos.
2. Seleccionar los 5 mejores para inversión en alquiler.
3. Devolver ÚNICAMENTE un JSON válido con este formato EXACTO:

[
  {{
    "titulo": "string",
    "precio": 0,
    "alquiler_estimado": 0,
    "rentabilidad_neta": 0,
    "hipoteca_mensual": 0,
    "cashflow_anual": 0,
    "url": "string",
    "comentario": "breve explicación de por qué este piso es buena inversión"
  }}
]
"""

    response = openai.ChatCompletion.create(
        model="gpt-4.1-mini",  # usa el modelo que tengas disponible
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )

    content = response["choices"][0]["message"]["content"].strip()

    try:
        data = json.loads(content)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        if "```" in content:
            parts = content.split("```")
            if len(parts) >= 2:
                candidate = parts[-2].replace("json", "").strip()
                try:
                    data = json.loads(candidate)
                    if isinstance(data, list):
                        return data
                except json.JSONDecodeError:
                    pass

    return [{"error": "No se pudo parsear la respuesta de OpenAI", "raw": content}]


# --------------------------
# UI HTML SENCILLA (fondo blanco + buscador grande)
# --------------------------

@app.get("/", response_class=HTMLResponse)
def home():
    html = """
    <!doctype html>
    <html lang="es">
    <head>
      <meta charset="utf-8">
      <title>Inmobiliario GPT · Análisis de pisos</title>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <style>
        :root {
          --bg: #ffffff;
          --ink: #111111;
          --muted: #666666;
          --border: #dddddd;
        }
        * { box-sizing: border-box; }
        body {
          margin: 0;
          padding: 0;
          font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          background: var(--bg);
          color: var(--ink);
        }
        .page {
          min-height: 100vh;
          display: flex;
          flex-direction: column;
          align-items: center;
          padding: 40px 16px;
        }
        .container {
          width: 100%;
          max-width: 900px;
        }
        h1 {
          font-size: 2.4rem;
          margin-bottom: 8px;
          text-align: center;
        }
        .subtitle {
          font-size: 1rem;
          color: var(--muted);
          text-align: center;
          margin-bottom: 32px;
        }
        form {
          display: flex;
          flex-direction: column;
          gap: 12px;
          margin-bottom: 28px;
        }
        .search-row {
          display: flex;
          flex-direction: row;
          gap: 12px;
          width: 100%;
        }
        .search-input {
          flex: 1;
          padding: 14px 16px;
          font-size: 1.1rem;
          border-radius: 999px;
          border: 1px solid var(--border);
          outline: none;
        }
        .search-input:focus {
          border-color: #000000;
        }
        .button {
          padding: 14px 20px;
          font-size: 1rem;
          border-radius: 999px;
          border: 1px solid #000000;
          background: #000000;
          color: #ffffff;
          cursor: pointer;
          white-space: nowrap;
        }
        .button:hover {
          opacity: 0.9;
        }
        .filters {
          display: flex;
          flex-wrap: wrap;
          gap: 12px;
          font-size: 0.9rem;
        }
        .filters input {
          padding: 8px 10px;
          font-size: 0.9rem;
          border-radius: 999px;
          border: 1px solid var(--border);
        }
        .results {
          margin-top: 24px;
        }
        .card {
          border: 1px solid var(--border);
          border-radius: 16px;
          padding: 16px 18px;
          margin-bottom: 12px;
        }
        .card-title {
          font-size: 1rem;
          font-weight: 600;
          margin-bottom: 6px;
        }
        .card-line {
          font-size: 0.9rem;
          margin-bottom: 3px;
        }
        .card a {
          color: #000000;
          text-decoration: underline;
          font-size: 0.9rem;
        }
        .badge {
          display: inline-block;
          font-size: 0.75rem;
          padding: 2px 8px;
          border-radius: 999px;
          border: 1px solid var(--border);
          margin-right: 6px;
        }
        @media (max-width: 640px) {
          .search-row {
            flex-direction: column;
          }
          .button {
            width: 100%;
            text-align: center;
          }
        }
      </style>
    </head>
    <body>
      <div class="page">
        <div class="container">
          <h1>Inmobiliario GPT</h1>
          <p class="subtitle">
            Escribe qué tipo de piso buscas y analizamos rentabilidad, hipoteca y cashflow.
          </p>

          <form id="search-form">
            <div class="search-row">
              <input
                id="query"
                class="search-input"
                type="text"
                placeholder="Ej. Pisos en Málaga por debajo de 120k para alquilar"
                required
              />
              <button class="button" type="submit">Analizar pisos</button>
            </div>

            <div class="filters">
              <div>
                <label>Ciudad&nbsp;</label>
                <input id="ciudad" type="text" value="Madrid" />
              </div>
              <div>
                <label>Precio máx (€)&nbsp;</label>
                <input id="precio_max" type="number" value="200000" />
              </div>
              <div>
                <label>Reforma (€)&nbsp;</label>
                <input id="reforma" type="number" value="0" />
              </div>
              <div>
                <label>Para alquilar</label>
                <input id="for_rent" type="checkbox" checked />
              </div>
            </div>
          </form>

          <div id="status" class="subtitle" style="margin-top:0;"></div>
          <div id="results" class="results"></div>
        </div>
      </div>

      <script>
        const form = document.getElementById("search-form");
        const statusEl = document.getElementById("status");
        const resultsEl = document.getElementById("results");

        form.addEventListener("submit", async (e) => {
          e.preventDefault();
          const query = document.getElementById("query").value.trim();
          const ciudad = document.getElementById("ciudad").value.trim() || "Madrid";
          const precio_max = document.getElementById("precio_max").value || "200000";
          const reforma = document.getElementById("reforma").value || "0";
          const for_rent = document.getElementById("for_rent").checked ? "true" : "false";

          if (!query) return;

          statusEl.textContent = "Analizando pisos...";
          resultsEl.innerHTML = "";

          try {
            const params = new URLSearchParams({
              query,
              ciudad,
              precio_max: String(precio_max),
              reforma: String(reforma),
              for_rent,
            });

            const resp = await fetch("/analizar?" + params.toString());
            if (!resp.ok) {
              statusEl.textContent = "Error al llamar a la API (" + resp.status + ")";
              return;
            }
            const data = await resp.json();
            statusEl.textContent =
              "Analizados " + (data.total_analizados || 0) + " pisos. Mostrando TOP 5:";

            if (!data.top5 || !Array.isArray(data.top5) || data.top5.length === 0) {
              resultsEl.innerHTML = "<p>No se encontraron pisos adecuados.</p>";
              return;
            }

            const itemsHtml = data.top5
              .map((p, idx) => {
                const titulo = p.titulo || "Piso sin título";
                const precio = p.precio != null ? p.precio.toLocaleString("es-ES") + " €" : "—";
                const alquiler = p.alquiler_estimado != null ? p.alquiler_estimado.toLocaleString("es-ES") + " €/mes" : "—";
                const rentNeta = p.rentabilidad_neta != null ? p.rentabilidad_neta + " %" : "—";
                const hipoteca = p.hipoteca_mensual != null ? p.hipoteca_mensual.toLocaleString("es-ES") + " €/mes" : "—";
                const cashflow = p.cashflow_anual != null ? p.cashflow_anual.toLocaleString("es-ES") + " €/año" : "—";
                const comentario = p.comentario || "";
                const url = p.url || "#";

                return `
                  <div class="card">
                    <div class="card-title">#${idx + 1} · ${titulo}</div>
                    <div class="card-line">
                      <span class="badge">Precio: ${precio}</span>
                      <span class="badge">Alquiler estimado: ${alquiler}</span>
                    </div>
                    <div class="card-line">
                      <span class="badge">Rent. neta: ${rentNeta}</span>
                      <span class="badge">Hipoteca: ${hipoteca}</span>
                      <span class="badge">Cashflow: ${cashflow}</span>
                    </div>
                    <div class="card-line">
                      ${comentario ? comentario : ""}
                    </div>
                    ${
                      url && url !== "#"
                        ? `<div class="card-line"><a href="${url}" target="_blank" rel="noopener noreferrer">Ver anuncio</a></div>`
                        : ""
                    }
                  </div>
                `;
              })
              .join("");

            resultsEl.innerHTML = itemsHtml;
          } catch (err) {
            console.error(err);
            statusEl.textContent = "Error inesperado al analizar pisos.";
          }
        });
      </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


# --------------------------
# ENDPOINTS API
# --------------------------

@app.get("/health")
def health():
    return {"status": "ok", "message": "Inmobiliario GPT API funcionando"}


@app.get(
    "/analizar",
    response_model=AnalysisResponse,
    summary="Analizar pisos y obtener TOP 5 por rentabilidad",
)
def analizar(
    query: str = Query(...),
    ciudad: str = Query("Madrid"),
    precio_max: int = Query(200000),
    hipoteca_pct: float = Query(0.8),
    interes: float = Query(0.03),
    anos: int = Query(25),
    reforma: int = Query(0),
    limit: int = Query(20),
    for_rent: bool = Query(True),
):
    raw_items = fetch_properties_from_apify(ciudad=ciudad, max_price=precio_max, limit=limit, for_rent=for_rent)
    mapped: List[PropertyInput] = [map_raw_item_to_property(item, ciudad) for item in raw_items]

    enriched: List[PropertyEnriched] = []

    for p in mapped:
        if not p.precio or p.precio <= 0:
            continue

        hipoteca_capital = p.precio * hipoteca_pct
        cuota_mensual = mortgage_payment(hipoteca_capital, interes, anos)
        yields = calculate_yields(price=p.precio, rent_monthly=p.alquiler_estimado, reforma=reforma)
        cashflow_anual = p.alquiler_estimado * 12.0 - cuota_mensual * 12.0 - (reforma / 5.0 if reforma > 0 else 0.0)

        enriched.append(
            PropertyEnriched(
                titulo=p.titulo,
                precio=p.precio,
                alquiler_estimado=p.alquiler_estimado,
                url=p.url,
                ciudad=p.ciudad,
                habitaciones=p.habitaciones,
                banos=p.banos,
                m2=p.m2,
                rentabilidad_bruta=yields["rent_bruta"],
                rentabilidad_neta=yields["rent_neta"],
                hipoteca_mensual=round(cuota_mensual, 2),
                cashflow_anual=round(cashflow_anual, 2),
            )
        )

    if not enriched:
        return AnalysisResponse(
            query=query,
            ciudad=ciudad,
            total_analizados=0,
            top5=[],
            raw=enriched,
        )

    pisos_dicts = [e.model_dump() for e in enriched]
    top5 = rank_properties_with_ai(query, pisos_dicts)

    return AnalysisResponse(
        query=query,
        ciudad=ciudad,
        total_analizados=len(enriched),
        top5=top5,
        raw=enriched,
    )


# --------------------------
# ENTRYPOINT LOCAL
# --------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
