import { Actor } from 'apify';

//
// Actor Buscar Pisos
// - Input: { ciudad, precio_max, for_rent }
// - Llama al actor igolaizola/idealista-scraper con filtros
// - Devuelve el mismo dataset que ese actor
//

await Actor.init();

// 1. Leer input
const input = await Actor.getInput() || {};
const ciudad = input.ciudad || "madrid";
const maxPrice = input.precio_max || 200000;
const forRent = input.for_rent || false;

// 2. Construir filtros para el actor de Idealista
function buildIdealistaInput(ciudad, maxPrice, forRent) {
    const operation = forRent ? "rent" : "sale";

    return {
        // nº máximo de pisos
        maxItems: 50,

        // compra o alquiler
        operation, // "sale" | "rent"

        // tipo de propiedad (dejamos el default de “Homes”)
        propertyType: "Homes",

        // país
        country: "Spain",

        // rango de precio
        minPrice: 0,
        maxPrice: maxPrice || undefined,

        // intentamos pasar la ciudad como texto de localización;
        // si el actor no lo usa, simplemente la ignorará.
        locationQuery: ciudad,

        // otros filtros se quedan por defecto
    };
}

const childInput = buildIdealistaInput(ciudad, maxPrice, forRent);
console.log("🔎 Llamando a igolaizola/idealista-scraper con:", childInput);

// 3. Llamar al actor de Idealista
const { defaultDatasetId } = await Actor.call("igolaizola/idealista-scraper", childInput);

// 4. Leer los resultados que ha scrapeado
const { items } = await Actor.getDatasetItems(defaultDatasetId);
console.log(`✅ Recibidos ${items.length} pisos de Idealista`);

// 5. Devolverlos como salida de este actor
if (items && items.length > 0) {
    await Actor.pushData(items);
} else {
    console.warn("No se encontraron pisos para la búsqueda dada.");
}

await Actor.exit();
