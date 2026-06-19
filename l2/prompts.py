"""Phase 0 prompt: open-ended taxonomy discovery.

No fixed taxonomy. The model proposes L1/L2/L3, major_bulk, minor_bulk freely.
We cluster the outputs across 60 rows to decide the locked taxonomy in Phase 1.
"""

PHASE0_SYSTEM = (
    "You are a travel content classifier for a POI (point-of-interest) intelligence system. "
    "You have NO fixed taxonomy — you propose what makes sense for each piece of content. "
    "Output strict JSON only. Be precise. Reason about taxonomy gaps."
)

PHASE0_USER_TEMPLATE = """POI: {poi_name}, {city}
SOURCE: {source}
TEXT:
\"\"\"{text}\"\"\"

Classify this content. Return JSON with this exact shape:

{{
  "l1": "<broadest domain, 1-2 words snake_case>",          // e.g. experience | logistics | warnings | context | food
  "l2": "<subcategory, snake_case>",                          // e.g. crowds | tickets | scams | history | nearby_food
  "l3": "<specific aspect, snake_case or null>",              // e.g. entry_queue | skip_the_line | pickpockets

  "major_bulk": "<one broad usefulness bucket, kebab-case>",  // propose: things-to-do | places-to-eat |
                                                              // scams-to-avoid | logistics-tips | historical-context |
                                                              // photo-spots | family-tips | OR a new one you propose
  "minor_bulk": ["<list of specific aspects this is useful for, snake_case>"],
                                                              // e.g. ["best_time_to_visit", "audio_guide_recommended"]

  "topic_sentiments": [
    {{"topic": "<topic_snake_case>", "sentiment": "positive|neutral|negative", "strength": 0.0}}
  ],
  "overall_sentiment": "positive|neutral|negative|mixed",
  "is_relevant": true,                                        // useful for someone planning to visit?
  "key_insight": "<one sentence takeaway>",

  "reasoning": "<why these L1/L2/L3; what alternatives you considered>",
  "suggested_taxonomy": "<l1/l2/l3 path if a different one would fit better, else empty string>",
  "confidence": 0.0                                            // 0.0-1.0
}}

Rules:
- Snake_case for L1/L2/L3 and minor_bulk; kebab-case for major_bulk.
- If text is junk (spam, off-topic, single emoji), set is_relevant=false.
- key_insight must be a single, actionable sentence — what should a visitor take away?
- suggested_taxonomy: be honest. If your L1/L2/L3 is the best fit, leave it as "". If you think the corpus would benefit from a new path, propose it.
- confidence reflects how clear-cut the classification was (not your general capability).
"""


def build_phase0_prompt(poi_name: str, city: str, source: str, text: str) -> tuple[str, str]:
    return PHASE0_SYSTEM, PHASE0_USER_TEMPLATE.format(
        poi_name=poi_name,
        city=city,
        source=source,
        text=text,
    )
