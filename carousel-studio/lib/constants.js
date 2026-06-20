// Curated brand vocabulary. The Design Director can ONLY pick from these lists.
// Free-form LLM design choices are how AI slop happens. This file is the wedge.

export const PALETTES = [
  {
    id: "roman_stone",
    name: "Roman Stone",
    bg: "#F4EFE6",        // cream paper
    fg: "#1A1614",        // ink
    accent: "#7A1F1F",    // oxblood
    muted: "#8A8478",     // stone
    mood: "Lapham's Quarterly · weight of antiquity · oxblood used sparingly on rules and drop caps",
  },
  {
    id: "cobalt_press",
    name: "Cobalt Press",
    bg: "#FBFAF7",        // off-white
    fg: "#0B0B0B",        // black
    accent: "#1B4D9B",    // cobalt
    muted: "#7A7A7A",
    mood: "FT Weekend · cobalt headlines · numerical, declarative",
  },
  {
    id: "olive_maroon",
    name: "Olive & Maroon",
    bg: "#EFE8DA",        // bone
    fg: "#1B1A16",
    accent: "#5C1F1F",    // maroon
    muted: "#3D4A22",     // olive
    mood: "Apartamento · earthen · two-color rule, never more",
  },
  {
    id: "newsprint",
    name: "Newsprint",
    bg: "#E8E4DA",        // newsprint
    fg: "#0F0F0F",
    accent: "#D33B27",    // vermillion stamp
    muted: "#6B6B6B",
    mood: "Tabloid front page · vermillion only for the stamp · slightly off-register feel",
  },
  {
    id: "vatican_plaster",
    name: "Vatican Plaster",
    bg: "#E8DCC4",        // plaster
    fg: "#2C2A28",        // slate
    accent: "#C25E3B",    // terracotta
    muted: "#867D6E",
    mood: "Roman fresco · sun-warmed plaster · terracotta accent · architectural",
  },
];

export const TYPE_PAIRINGS = [
  {
    id: "fraunces_newsreader",
    name: "Fraunces / Newsreader / JetBrains",
    display: { family: "Fraunces", weight: 700, opsz: 144, soft: 100, italic: false },
    body: { family: "Newsreader", weight: 400, italic: true },
    mono: { family: "JetBrains Mono", weight: 500, italic: false },
    google_fonts: "Fraunces:opsz,wght@9..144,500;9..144,700|Newsreader:ital,wght@0,400;1,400|JetBrains+Mono:wght@500",
    rationale: "Fraunces opsz 144 carries display weight without looking machined; Newsreader italic is the editorial body voice; mono for codes and metadata.",
  },
  {
    id: "sectra_plex",
    name: "GT Sectra / IBM Plex Sans / IBM Plex Mono",
    display: { family: "Crimson Pro", weight: 700, italic: false },
    body: { family: "IBM Plex Sans", weight: 400, italic: false },
    mono: { family: "IBM Plex Mono", weight: 500, italic: false },
    google_fonts: "Crimson+Pro:wght@700|IBM+Plex+Sans:wght@400;500|IBM+Plex+Mono:wght@500",
    rationale: "Crimson Pro as a Sectra-adjacent display; Plex Sans is institutional, declarative; mono ties the family.",
  },
  {
    id: "editorial_sohne",
    name: "Editorial New / Söhne / Berkeley Mono",
    display: { family: "EB Garamond", weight: 800, italic: false },
    body: { family: "Inter Tight", weight: 400, italic: false },
    mono: { family: "JetBrains Mono", weight: 400, italic: false },
    google_fonts: "EB+Garamond:wght@800|Inter+Tight:wght@400;500|JetBrains+Mono:wght@400",
    rationale: "Garamond as Editorial-New-adjacent serif display; Inter Tight reads sober without being cold; mono for grids.",
  },
  {
    id: "reckless_inter",
    name: "Reckless / Inter Display / Berkeley Mono",
    display: { family: "Newsreader", weight: 800, italic: false },
    body: { family: "Inter", weight: 500, italic: false },
    mono: { family: "JetBrains Mono", weight: 500, italic: false },
    google_fonts: "Newsreader:wght@800|Inter:wght@500;700|JetBrains+Mono:wght@500",
    rationale: "Reckless-adjacent display tension with Inter's screen-honesty; the conflict is the point.",
  },
  {
    id: "garamond_inter",
    name: "EB Garamond / Inter / JetBrains Mono",
    display: { family: "EB Garamond", weight: 700, italic: true },
    body: { family: "Inter", weight: 400, italic: false },
    mono: { family: "JetBrains Mono", weight: 400, italic: false },
    google_fonts: "EB+Garamond:ital,wght@1,700|Inter:wght@400;600|JetBrains+Mono:wght@400",
    rationale: "Garamond italic display gives 18th-century print feel; Inter body keeps it legible on a phone screen.",
  },
];

export const LAYOUTS = [
  {
    id: "silhouette_overlay",
    name: "Silhouette Overlay",
    description: "Full-bleed silhouette of monument against accent color, headline bottom-left in display type, mono caption top-right",
    composition: "rule of thirds, monument silhouette occupies right two-thirds, type left third with generous breathing room",
    best_for: ["cover", "outro"],
  },
  {
    id: "map_pin",
    name: "Map Pin",
    description: "Engraved-style vintage paper map of the surrounding district, single accent-colored pin on the attraction, headline overlaid bottom",
    composition: "map fills entire frame at low contrast, pin pops in accent color, headline bottom-third with mono coordinates above it",
    best_for: ["context", "tip"],
  },
  {
    id: "pull_quote",
    name: "Pull Quote",
    description: "Solid bg color, oversized display-italic quotation dominates center, attribution in mono below",
    composition: "quote takes 75% of canvas height, em-dash + attribution in 14pt mono, opening quotation mark hangs left",
    best_for: ["pull_quote"],
  },
  {
    id: "data_number",
    name: "Data Number",
    description: "Single massive numeral in display italic, caption clause underneath in body type, mono source line at foot",
    composition: "numeral occupies top 60%, caption mid, mono source bottom — generous whitespace around the numeral",
    best_for: ["data"],
  },
  {
    id: "vs_compare",
    name: "Versus Compare",
    description: "Two columns separated by a 1px vertical rule in accent color, label A vs label B at top, three short pros under each",
    composition: "vertical mono rule dead-center, headlines in display, pros in body, 'vs' set in italic at midpoint",
    best_for: ["comparison"],
  },
  {
    id: "receipt_card",
    name: "Receipt Card",
    description: "Off-white ticket stub aesthetic with perforated edge, mono ticket details, accent stamp impression",
    composition: "perforated dashed line at top, ticket info in mono grid, accent stamp diagonal upper-right",
    best_for: ["data", "outro"],
  },
  {
    id: "polaroid",
    name: "Polaroid",
    description: "Square photo with white border, handwritten-style caption below in script or italic display",
    composition: "85% photo / 15% caption strip, slight rotation 1-2 degrees, slight shadow",
    best_for: ["context", "outro"],
  },
  {
    id: "boarding_pass",
    name: "Boarding Pass",
    description: "Departure-board geometry with diagonal divisions, all-mono codes (FROM/TO/GATE/TIME), accent flight number",
    composition: "horizontal layout with three diagonal panels, mono throughout, accent only on flight code",
    best_for: ["tip", "cover"],
  },
  {
    id: "magazine_cover",
    name: "Magazine Cover",
    description: "Masthead band at top, single subject image center, screaming headline overlaid bottom — Lapham's-style",
    composition: "masthead 12% top, image 60% middle, headline 28% bottom with rule above",
    best_for: ["cover"],
  },
  {
    id: "index_card",
    name: "Index Card",
    description: "Bordered card with horizontal ruled lines like a recipe card, structured field rows (HOW LONG / WHEN / TIP)",
    composition: "thin accent border, three ruled rows with mono labels and body answers",
    best_for: ["tip", "context"],
  },
];

export const SLIDE_KINDS = [
  { id: "cover", role: "hook — earn the first second" },
  { id: "context", role: "set the stage in one breath" },
  { id: "tip", role: "one concrete actionable insight" },
  { id: "pull_quote", role: "real reviewer voice that lands the point" },
  { id: "data", role: "one specific number people will screenshot" },
  { id: "comparison", role: "a vs b decision" },
  { id: "outro", role: "memorable closer or CTA" },
];

export const ANTI_SLOP_RULES = [
  "no purple-to-blue gradient backgrounds",
  "no glassmorphism / no frosted glass card",
  "no neon glow / no light leaks",
  "no generic sans-serif text overlay on stock photography",
  "no saturation boost — palette colors only, at their stated hex",
  "no AI-generated faces unless deliberately silhouetted",
  "no emoji rendered inside the image",
  "no decorative scrollwork / no Canva flourishes",
  "no '5 tips for X' style enumerated headings",
  "no patronizing 'did you know' tone",
];

// Travel-IG editorial context fed to all agents so they share an aesthetic vocabulary.
export const TRAVEL_IG_CONTEXT = `
Reference aesthetic: think Atlas Obscura's Instagram, Cereal magazine, Boat magazine, Suitcase, the Wallpaper city guides.
What good travel carousels do:
- Slide 1 is a hook that creates a curiosity gap or stakes a contrarian claim — not a brand splash.
- Each subsequent slide does ONE job (a fact, a quote, a tip, a number) — no slide tries to do two things.
- Numbers and specifics beat vague claims ("queue at 8:42am" beats "go early").
- Real reviewer quotes carry more trust than LLM-generated tips. Attribute them ("— k/r/Rome, 23 Mar 2026").
- Final slide is either a memorable closer or a soft CTA, not a hard sell.
- Visual vocabulary: editorial typography, constrained palettes, generous whitespace, asymmetric balance.
- Travel ≠ sunsets and palm trees. Travel = the texture of a specific place at a specific time.
Counter-references (do not produce work resembling these): generic Canva 5-tip carousels, sunset silhouette quote graphics with the attraction obscured by gradient, hyper-saturated cinematic stock with thin sans-serif overlay.
`.trim();

// Defaults pulled from env at request time
export const MODELS = {
  text_heavy: process.env.TEXT_MODEL_HEAVY || "gpt-4o",
  text_light: process.env.TEXT_MODEL_LIGHT || "gpt-4o-mini",
  image: process.env.IMAGE_MODEL || "gpt-image-2",
};
