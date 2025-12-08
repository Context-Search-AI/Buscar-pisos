from fastapi import FastAPI
import os
import requests
import time

app = FastAPI()

APIFY_TOKEN = os.getenv("APIFY_TOKEN")
ACTOR_ID = os.getenv("APIFY_ACTOR_ID")


@app.get("/buscar")
def buscar(ciudad: str = "madrid", precio_max: int = 200000, for_rent: bool = False):

    run_input = {
        "ciudad": ciudad,
        "precio_max": precio_max,
        "for_rent": for_rent
    }

    # 1. Lanzar el actor
    start_url = f"https://api.apify.com/v2/acts/{ACTOR_ID}/runs?token={APIFY_TOKEN}"

    response = requests.post(start_url, json=run_input).json()
    run_id = response["data"]["id"]

    # 2. Polling hasta que termine el scraping
    status_url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={APIFY_TOKEN}"

    while True:
        status = requests.get(status_url).json()
        if status["data"]["status"] in ["SUCCEEDED", "FAILED"]:
            break
        time.sleep(1)

    if status["data"]["status"] == "FAILED":
        return {"error": "Scrapeo falló"}

    dataset_id = status["data"]["defaultDatasetId"]

    # 3. Obtener dataset final
    items_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={APIFY_TOKEN}"
    items = requests.get(items_url).json()

    # 4. Devolver pisos
    return {
        "ciudad": ciudad,
        "cantidad": len(items),
        "pisos": items
    }

from fastapi.responses import FileResponse

@app.get("/")
def ui():
    return FileResponse("ui.html")
