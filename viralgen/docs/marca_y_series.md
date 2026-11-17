# Marca pública y series de contenido: dos capas distintas

Antes de tocar nada, revisión de dónde aparece **«Bosque Lumina»** en el
repositorio. El resultado cambia la respuesta, así que va primero.

## Dónde aparece hoy

| Archivo | Qué es |
|---|---|
| `src/viralgen/data/series_bible.json` | `series_id: "bosque_lumina"`, `title: "El bosque de Lumina"`, su `style_prompt`, su paleta, su `negative_prompt` y sus tres personajes: `lumi` (zorra), `tobi` (erizo), `nube` (búho) |
| `src/viralgen/data/profiles.json` | el perfil `infantil_cuentos` apunta a esa biblia con `series_bible_id: "bosque_lumina"` |
| `examples/perfiles_solo_imagenes.json` | el perfil de ejemplo del piloto, con la misma referencia |
| `tests/test_profiles.py`, `tests/test_media_plan.py` | resuelven la biblia por ese identificador |

## La conclusión: son dos cosas, no una

**«Nubaria Cuentos» es la marca pública**: el canal de YouTube,
`@nubariacuentos`, la cuenta sobre la que se publica. Vive en la capa de
publicación.

**«El bosque de Lumina» es una serie de contenido**: el mundo, el estilo
visual y los personajes de los cuentos. Vive en la biblia de series.

Un canal puede alojar varias series; una serie puede no publicarse nunca. No
son sinónimos y no se sustituyen.

## Por eso NO se renombra `bosque_lumina`

Renombrar ese identificador no es un cambio cosmético:

1. **Rompe la resolución de perfiles.** `profiles.json` y el perfil de ejemplo
   lo referencian por cadena exacta; `get_series_bible` falla si no existe.
2. **Invalida todos los assets ya generados.** El `style_prompt` y las
   descripciones de personaje entran en `effective_prompt`, que entra en
   `image_cache_key`. Cambiar el texto cambia la clave: todas las imágenes en
   caché dejan de encontrarse y habría que volver a pagarlas.
3. **Invalida la huella del trabajo de medios.** `_fingerprint` incluye
   `reference_selection`, que deriva de la biblia.
4. **Deja huérfanos los documentos ya generados.** Un `script.json` existente
   lleva `character_ids: ["lumi", "tobi", "nube"]`. Renombrar los personajes
   rompe la correspondencia con sus propios medios y su voz.

Y sobre los personajes en particular: **`nube` es el búho de la serie**, no una
abreviatura de «Nubaria». La coincidencia de sílaba es casual. Renombrarlo
«porque suena a la marca» rompería el punto 4 sin ganar nada.

## Dónde sí va la marca

En el **catálogo de cuentas** de la capa de publicación, que es exactamente
donde el módulo 5 espera la identidad del destino:

```json
{
  "accounts": [
    {
      "alias": "nubaria",
      "platform": "youtube_shorts",
      "account_id": "<el ID exacto del canal, empieza por UC>",
      "label": "Nubaria Cuentos (@nubariacuentos)"
    },
    {
      "alias": "rarezas_ig",
      "platform": "instagram_reels",
      "account_id": "<instagram_user_id>",
      "label": "Rarezas al Minuto (@rarezasalminuto)"
    },
    {
      "alias": "rarezas_tt",
      "platform": "tiktok",
      "account_id": "@rarezasalminuto",
      "label": "Rarezas al Minuto (exportación manual)"
    }
  ]
}
```

El `alias` es local y se usa en los comandos; el `account_id` es el que la
plataforma tiene que confirmar antes de cualquier envío real (`accounts check`
compara lo observado con lo esperado y bloquea si no coinciden). El `label` es
para que un humano reconozca la cuenta en un informe.

Nótese que las dos marcas son distintas y van a plataformas distintas:
**Nubaria Cuentos** en YouTube (cuentos infantiles, serie «El bosque de
Lumina») y **Rarezas al Minuto** en Instagram y TikTok (curiosidades, serie
«La mesa de las curiosidades»). El perfil `infantil_cuentos` alimenta la
primera; `curiosidades_corto` y `curiosidades_largo`, la segunda.

## Si algún día se quiere renombrar de verdad

Sería un cambio deliberado, con su propia migración: nueva entrada en la biblia
con identificador nuevo, perfiles apuntando a ella, y los documentos antiguos
conservando el suyo. Nunca una sustitución de texto sobre los archivos de datos
y los documentos ya generados.
