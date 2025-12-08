import { Actor } from 'apify';

//
// Actor: Buscar Pisos
// - Input: { ciudad, precio_max, for_rent }
// - Llama al actor igolaizola/idealista-scraper
// - Devuelve como salida los mismos pisos que scrapea Idealista
//

// 1) Inicializar actor
await Actor.init();

// 2) Leer input con valores por defecto
const input = await Actor.getInput() || {};
const ciudad = input.ciudad || "madrid";
const maxPrice = input.precio_max || 200000;
const forRent = input.for_rent || false;

// 3) Construir el input para el actor de Idealista
function buildIdealistaInput(ciudad, maxPrice, forRent) {
    const operation = forRent ? "rent" : "sale"; // "sale" o "rent"

    return {
        // nº máximo de inmuebles a traer
        maxItems: 50,

        // compra o alquiler
        operation,             // "sale" | "rent"

        // tipo de propiedad (según el schema del actor: en minúsculas)
        propertyType: "homes", // "homes", "newDevelopments", "offices", etc.

        // país
        country: "Spain",

        // rango de precios
        minPrice: 0,
        maxPrice: maxPrice,

        // texto de localización (la ciudad)
        locationQuery: ciudad
    };
}

const childInput = buildIdealistaInput(ciudad, maxPrice, forRent);
console.log("🔎 Llamando a igolaizola/idealista-scraper con:", childInput);

// 4) Ejecutar el actor de Idealista
const { defaultDatasetId } = await Actor.call(
    "igolaizola/idealista-scraper",
    childInput
);

// 5) Leer los resultados del dataset que ha generado el scraper
const { items } = await Actor.getDatasetItems(defaultDatasetId);
console.log(`✅ Recibidos ${items.length} pisos de Idealista`);

// 6) Devolver los pisos como salida de este actor
if (items && items.length > 0) {
    await Actor.pushData(items);
} else {
    console.warn("No se encontraron pisos para la búsqueda dada.");
}

// 7) Finalizar
await Actor.exit();
