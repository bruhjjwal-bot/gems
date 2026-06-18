# Google Maps Place IDs

How we sourced the Place IDs for the current 6 POIs, and how to add new ones.

## The current 6 POIs

| POI | City | Place ID |
|---|---|---|
| Eiffel Tower | Paris | `ChIJLU7jZClu5kcR4PcOOO6p3I0` |
| Louvre Museum | Paris | `ChIJD3uTd9hx5kcR1IQvGfr8dbk` |
| Palace of Versailles | Paris | `ChIJdUyx15R95kcRj85ZX8H8OAU` |
| Colosseum | Rome | `ChIJrRMgU7ZhLxMRxAOFkC7I8Sg` |
| Trevi Fountain | Rome | `ChIJ1UCDJ1NgLxMRtrsCzOHxdvY` |
| Vatican Museums | Rome (Vatican City) | `ChIJKcGbg2NgLxMRthZkUqDs4M8` |

## How to find a new POI's Place ID

1. Go to **https://developers.google.com/maps/documentation/javascript/examples/places-placeid-finder**
2. Type the POI's full name (and city, to disambiguate landmark vs. nearby shops/restaurants)
3. Click the result on the map → an info card pops up with `Place ID: ChIJ…`
4. Copy just the `ChIJ…` part (no postal code, no place name appended — Google's clipboard sometimes includes extra)
5. Insert/update in Supabase:

```sql
UPDATE pois SET place_id = 'ChIJxxxxxxxxxxxxxxxxxxx' WHERE name = '<POI name>';
```

## Place ID gotchas

- **Format**: always starts with `ChIJ`, total length usually 27 characters (`ChIJ` + 23 alphanumerics). The Vatican Museums one we sourced (`ChIJKcGbg2NgLxMRthZkUqDs4M8`) matched this exact pattern.
- **Disambiguation**: searching "Louvre" alone returns the museum, but for less famous POIs you'll need the city. Searching "Versailles" returns the city, not the palace — use "Palace of Versailles" or "Château de Versailles".
- **Some POIs have nested entities with their own Place IDs**. The Eiffel Tower has separate Place IDs for the restaurant inside (Le Jules Verne) and various ticket booth locations. We want the main monument ID.
- **Landmarks vs. nearby venues**: places near landmarks (souvenir shops, gelaterias next to Trevi Fountain) also have Place IDs and ratings. Their `aria-label="X.X stars Y Reviews"` patterns will appear in the same Maps DOM. SerpApi avoids this by binding to the specific `place_id` we pass.

## Why not use the official Google Places API instead?

The official Places API ("Place Details" endpoint) only returns **5 reviews per place**, regardless of how many actually exist. Useless for our scale of work. SerpApi scrapes the public Maps UI server-side and gets the same ~5K per-place ceiling that the Maps web UI exposes.
