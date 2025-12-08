import { Actor } from 'apify';

//
// Actor Buscar Pisos
// - Input: { ciudad, precio_max, for_rent }
// - Construye una URL de Idealista
// - Llama al actor igolaizola/idealista-scraper con inputs válidos
// - Devuelve el dataset de Idealista tal cual
//

await Actor.init();

// Leer input
const input = await Actor.getInput() || {};
const ciudad = input.ciudad || "madrid";
const maxPrice = input.precio_max || 200000;
const forRent = input.for_rent || false;

// Construir INPUT válido para el scraper
function buildIdealistaInput(ciudad, maxPrice, forRent) {
    return {
        maxItems: 50,
        operation: forRent ? "rent" : "sale",   // sale | rent
        propertyType: "homes",                  // MUST be lowercase
        country: "es",                          // es | pt | it
        minPrice: 0,
        maxPrice: maxPrice,
        locationQuery: ciudad.toLowerCase()
    };
}

const idealistaInput = buildIdealistaInput(ciudad, maxPrice, forRent);

console.log("🔎 Enviando al Scraper de Idealista con:", idealistaInput);

// Llamar al actor scrapeador oficial
const { defaultDatasetId } = await Actor.call("igolaizola/idealista-scraper", idealistaInput);

console.log("📦 Dataset recibido:", defaultDatasetId);

// Leer datos
const { items } = await Actor.getDatasetItems(defaultDatasetId);

console.log(`✅ Recibidos ${items.length} pisos de Idealista`);

if (items && items.length > 0) {
    await Actor.pushData(items);
} else {
    console.warn("⚠️ No se encontraron resultados para la búsqueda.");
}

await Actor.exit();
