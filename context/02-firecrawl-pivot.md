# Why FireCrawl doesn't work for Google Maps reviews

Burnt ~1K FireCrawl credits over ~90 minutes proving this empirically. Preserving the lesson so the next person doesn't try the same path.

## TL;DR

Google Maps detects FireCrawl's headless browser and serves a degraded page (~7KB markdown, mostly UI chrome, with the literal text "No reviews" where review content should be). No combination of actions, JavaScript injection, stealth proxy, or alternate URL pattern surfaces the actual review content. **Use a dedicated tool (SerpApi, Apify, Outscraper) for Google reviews.** FireCrawl remains a fine tool for sites that don't aggressively bot-detect (Reddit, TripAdvisor, etc.).

## What we tried, in order

### 1. Click the Reviews tab via FireCrawl action

```python
ClickAction(selector="button[role='tab'][aria-label*='Reviews']")
```

**Result:** Element not found error. Inspecting the rendered HTML, landmark POIs (Trevi Fountain, Eiffel Tower) only have **two tabs** — Overview and About. No dedicated Reviews tab. The 2,649 visible review count comes from a rating chip on the Overview page, not a tab.

### 2. Click the rating chip via JS injection

```python
ExecuteJavascriptAction(script="""
  const b = document.querySelector('button[aria-label*="stars"][aria-label*="Reviews"]');
  if (b) b.click();
""")
```

**Result:** Click executed, but the reviews modal it should open doesn't materialize in the scraped DOM. Either the click is no-op'd by Google's bot-detection-aware event handlers, or the modal renders in a way FireCrawl's scraper doesn't capture.

### 3. Scroll the inner panel via JS

After (presumed) modal opening, target the scrollable reviews region:

```python
ExecuteJavascriptAction(script="""
  document.querySelectorAll('div[tabindex="-1"], div.m6QErb').forEach(c => {
    if (c.scrollHeight > c.clientHeight + 50) c.scrollTop = c.scrollHeight;
  });
""")
```

**Result:** No reviews surfaced. The "ago" / "stars" / author-name patterns expected in real review content didn't appear in the markdown.

### 4. Alternate URL: `search.google.com/local/reviews?placeid=XXX`

Google's documented reviews-page URL. **Result:** Redirects to the place's SERP (Google Search results), which shows the review *count* ("510,355 Google reviews") and links to a deeper `&si=AL3DRZ...` URL where reviews live — but the `si` parameter is opaque and unguessable per-place.

### 5. Stealth proxy mode (5x credit cost)

```python
fc.scrape(url, ..., proxy="stealth")
```

**Result:** Identical 7KB markdown to the standard proxy. Google's bot detection isn't beaten by stealth proxies for Maps.

## What it looks like when FireCrawl runs on Trevi Fountain Maps

```
Overview / About / Directions / Save / Nearby
Aqueduct-fed rococo fountain, designed by Nicola Salvi & completed in 1762
Piazza di Trevi, 00187 Roma RM, Italy
Open · Closes 10 PM
...
Suggest an edit
Add a photo
From the owner
[long block of "Night of Museums" promotional text]
No reviews                                ← !!!
Write a review
```

The "No reviews" line is the giveaway. The page lists tabs and amenities but flatly states no reviews exist, despite the place having 510K of them. This is Google serving a stripped page to a detected bot.

## Why SerpApi succeeds where FireCrawl fails

SerpApi runs Google searches through their own infrastructure (presumably with rotated residential IPs, real browser fingerprints, the works) and exposes a clean JSON API. Their `google_maps_reviews` engine returns:
- `review_id` (Google's stable opaque ID)
- `user.name`, `user.contributor_id`, `user.local_guide` (boolean)
- `rating` (float 1-5), `snippet` (review text), `extracted_snippet.original`
- `iso_date` (proper ISO 8601 timestamp), `iso_date_of_last_edit`
- `likes`, `images[]`, `source`

8 reviews per first-page call, up to 20 per paginated call via `next_page_token`. Pagination is token-based and deterministic — every call returns a strictly-disjoint page.

## When FireCrawl is still the right tool

- **Reddit** — old.reddit.com renders cleanly for FireCrawl. No bot wall. Good for the planned Reddit scraper.
- **TripAdvisor** — public review pages are scraping-tolerant enough.
- **Anywhere with structured public content and no enterprise bot defense** — corporate sites, docs, niche forums, blogs.

Don't reach for FireCrawl when:
- The target is one of the FAANG-tier services (Google, Meta, LinkedIn, Amazon, etc.) — they all have purpose-built anti-bot defenses that even stealth proxies don't beat reliably.
- You need pagination across many pages — FireCrawl bills per page (1 credit each, 5 with stealth) and there's no efficient bulk mode for paginated content.
- You need actions that depend on the bot-detection-aware portions of a target's DOM rendering correctly. We hit this with Google's review modal.
