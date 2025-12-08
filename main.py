import { Actor } from 'apify';

//
// Actor Buscar Pisos
// - Input: { ciudad, precio_max, for_rent }
// - Llama al actor dz_omar/idealista-scraper con una URL construida
// - Devuelve el mismo dataset de Idealista
//

await Actor.init();

const input = await Actor.getInput() || {};
const ciudad = input.ciudad || "madrid";
const maxPrice = input.precio_max || 200000;
const forRent = input.for_rent || false;

function buildIdealistaUrl(ciudad, maxPrice, forRent) {
    const slug = ciudad.toLowerCase().trim().replace(/ /g, "-");
    let base;

    if (forRent) {
        base = `https://www.idealista.com/alquiler-viviendas/${slug}/`;
    } else {
        base = `https://www.idealista.com/venta-viviendas/${slug}/`;
    }

    if (maxPrice && maxPrice > 0) {
        base += `precio-hasta_${maxPrice}/`;
    }

    return base;
}

const urlBusqueda = buildIdealistaUrl(ciudad, maxPrice, forRent);
console.log("🔎 Buscando en Idealista URL:", urlBusqueda);

// Llamar al actor de Idealista
const { defaultDatasetId } = await Actor.call("dz_omar/idealista-scraper", {
    Url: [urlBusqueda],
    proxyConfig: {
        useApifyProxy: true,
        apifyProxyGroups: ["RESIDENTIAL"],
    },
});

console.log("📦 Dataset ID de Idealista:", defaultDatasetId);

// Leer los resultados scrapeados
const { items } = await Actor.getDatasetItems(defaultDatasetId);
console.log(`✅ Recibidos ${items.length} pisos de Idealista`);

// Empujar esos mismos items como salida de este actor
if (items && items.length > 0) {
    await Actor.pushData(items);
} else {
    console.warn("No se encontraron pisos para la búsqueda dada.");
}

await Actor.exit();
