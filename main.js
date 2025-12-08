import { Actor } from 'apify';

//
// Actor Buscar Pisos
// - Input: { ciudad, precio_max, for_rent }
// - Llama a igolaizola/idealista-scraper con un input válido
// - Devuelve el dataset de Idealista tal cual
//

// 1) Inicializar actor
await Actor.init();

// 2) Leer input
const input = await Actor.getInput() || {};
const ciudad = input.ciudad || "madrid";
const maxPrice = input.precio_max || 200000;
const forRent = input.for_rent || false;

// 3) Construir el input para el scraper de Idealista
function buildIdealistaInput(ciudad, maxPrice, forRent) {
    return {
        maxItems: 50,
        operation: forRent ? "rent" : "sale", // "sale" | "rent"
        propertyType: "homes",                // en minúsculas
        country: "es",                        // "es" | "pt" | "it"
        // 👇 Estos campos los quiere como STRING
        minPrice: "0",
        maxPrice: maxPrice ? String(maxPrice) : undefined,
        locationQuery: ciudad.toLowerCase()
    };
}

const idealistaInput = buildIdealistaInput(ciudad, maxPrice, forRent);
console.log("🔎 Enviando al Scraper de Idealista con:", idealistaInput);

// 4) Llamar al actor de Idealista
const { defaultDatasetId } = await Actor.call(
    "igolaizola/idealista-scraper",
    idealistaInput
);

console.log("📦 Dataset recibido:", defaultDatasetId);

// 5) Leer los datos del dataset
const { items } = await Actor.getDatasetItems(defaultDatasetId);
console.log(`✅ Recibidos ${items.length} pisos de Idealista`);

// 6) Devolverlos como salida de este actor
if (items && items.length > 0) {
    await Actor.pushData(items);
} else {
    console.warn("⚠️ No se encontraron resultados para la búsqueda dada.");
}

// 7) Finalizar
await Actor.exit();
