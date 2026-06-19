import os
from dotenv import load_dotenv
load_dotenv()

from db.client import get_client
from scrapers.google_reviews import scrape_poi_google_reviews
from scrapers.tripadvisor import scrape_poi_tripadvisor
from scrapers.youtube import scrape_poi_youtube


def get_all_pois() -> list[dict]:
    db = get_client()
    return db.table("pois").select("id, name, place_id, ta_url").execute().data or []


def run_youtube(pois: list[dict]):
    for poi in pois:
        scrape_poi_youtube(poi_id=poi["id"], poi_name=poi["name"])


def run_google_reviews(pois: list[dict]):
    sort_modes = tuple(
        s.strip() for s in os.environ.get(
            "GOOGLE_SORT_MODES", "newest,lowest,highest"
        ).split(",")
        if s.strip()
    )
    calls_per_stream = int(os.environ.get("CALLS_PER_STREAM", "1"))
    pause = float(os.environ.get("PAUSE_SECONDS", "80"))

    print(
        f"Run config: sorts={sort_modes}, calls_per_stream={calls_per_stream}, "
        f"pause={pause}s between calls"
    )
    total_calls = 0
    total_saved = 0
    for poi in pois:
        if not poi.get("place_id"):
            print(f"\nSkipping {poi['name']}: no place_id")
            continue
        print(f"\n=== {poi['name']} ===")
        result = scrape_poi_google_reviews(
            poi["id"],
            poi["place_id"],
            sort_modes=sort_modes,
            calls_per_stream=calls_per_stream,
            page_pause_seconds=pause,
        )
        total_calls += result["calls"]
        total_saved += result["saved"]

    print(
        f"\nRun summary: {total_calls} SerpApi calls, {total_saved} review rows upserted. "
        f"Re-run to continue from saved cursors."
    )


def run_tripadvisor(pois: list[dict]):
    """Round-robin scrape: each POI gets one page per round, looping until
    every POI either hits the per-POI cap or is marked exhausted. Splits the
    FireCrawl credit budget evenly across all POIs regardless of how quickly
    any individual POI's reviews fall off."""
    pages_per_poi_cap = int(os.environ.get("TA_TOTAL_PAGES_PER_POI", "600"))
    throttle = float(os.environ.get("TA_THROTTLE_SECONDS", "2"))
    max_error_streak = int(os.environ.get("TA_MAX_ERROR_STREAK", "3"))
    per_poi_review_cap = int(os.environ.get("TA_PER_POI_REVIEW_CAP", "0"))

    db = get_client()
    candidates = [p for p in pois if p.get("ta_url")]

    # Optional POI restriction (set via TA_POI_NAMES="A,B,C"). Lets two
    # concurrent processes split disjoint POI sets and avoid cursor races.
    name_filter_raw = os.environ.get("TA_POI_NAMES", "").strip()
    if name_filter_raw:
        allowed = {n.strip() for n in name_filter_raw.split(",") if n.strip()}
        candidates = [p for p in candidates if p["name"] in allowed]
        print(f"  TA_POI_NAMES filter active: {sorted(allowed)}; {len(candidates)} POIs in scope")
        if not candidates:
            print("  no POIs matched the filter; nothing to do")
            return

    pages_done = {p["id"]: 0 for p in candidates}
    error_streak = {p["id"]: 0 for p in candidates}
    review_count = {p["id"]: 0 for p in candidates}
    skip: set[str] = set()

    # Initial review counts so per-POI cap (if set) is enforced from row 1.
    if per_poi_review_cap > 0:
        for poi in candidates:
            res = (
                db.table("tripadvisor_reviews")
                .select("id", count="exact")
                .eq("poi_id", poi["id"])
                .limit(1)
                .execute()
            )
            review_count[poi["id"]] = res.count or 0
            if review_count[poi["id"]] >= per_poi_review_cap:
                skip.add(poi["id"])
                print(
                    f"  {poi['name']}: already at {review_count[poi['id']]}/"
                    f"{per_poi_review_cap}; skipping for this run"
                )

    print(
        f"TripAdvisor round-robin: cap={pages_per_poi_cap} pages/POI, "
        f"throttle={throttle}s, max_error_streak={max_error_streak}, "
        f"per_poi_review_cap={per_poi_review_cap or 'unlimited'}"
    )
    print(
        f"  even share across {len(candidates)} POIs ⇒ up to "
        f"{len(candidates) * pages_per_poi_cap} pages × 5 credits = "
        f"{len(candidates) * pages_per_poi_cap * 5} credits"
    )

    total_saved = 0
    total_calls = 0
    for round_no in range(pages_per_poi_cap):
        for poi in candidates:
            if poi["id"] in skip:
                continue
            if pages_done[poi["id"]] >= pages_per_poi_cap:
                continue
            print(
                f"\n[round {round_no + 1}/{pages_per_poi_cap}] "
                f"{poi['name']} (page {pages_done[poi['id']] + 1})"
            )
            result = scrape_poi_tripadvisor(
                poi["id"],
                poi["ta_url"],
                pages_per_run=1,
                throttle_seconds=throttle,
            )
            total_calls += result["pages"]
            total_saved += result["saved"]
            if result["exhausted"]:
                skip.add(poi["id"])
                print(f"  ⮑  cursor marked exhausted; skipping for rest of run")
            elif result["pages"] == 0:
                # Fetch error or transient block — cursor preserved at same offset.
                error_streak[poi["id"]] += 1
                if error_streak[poi["id"]] >= max_error_streak:
                    skip.add(poi["id"])
                    print(
                        f"  ⮑  {max_error_streak} consecutive errors; "
                        f"skipping for rest of run (cursor preserved)"
                    )
            else:
                error_streak[poi["id"]] = 0
                pages_done[poi["id"]] += result["pages"]
                review_count[poi["id"]] += result["saved"]
                if (
                    per_poi_review_cap > 0
                    and review_count[poi["id"]] >= per_poi_review_cap
                ):
                    skip.add(poi["id"])
                    print(
                        f"  ⮑  hit per-POI review cap "
                        f"({review_count[poi['id']]}/{per_poi_review_cap})"
                    )
        if all(
            p["id"] in skip or pages_done[p["id"]] >= pages_per_poi_cap
            for p in candidates
        ):
            print("\nAll POIs skipped or capped — ending.")
            break

    print("\n=== Final pages this run, per POI ===")
    for poi in candidates:
        state = "exhausted" if poi["id"] in skip else "active"
        print(f"  {poi['name']}: {pages_done[poi['id']]} pages ({state})")
    print(
        f"\nTripAdvisor run summary: {total_calls} FireCrawl calls "
        f"(~{total_calls * 5} credits), {total_saved} reviews upserted."
    )


def main():
    pois = get_all_pois()
    if not pois:
        print("No POIs found. Insert rows into the `pois` table first.")
        return

    scrapers = tuple(
        s.strip() for s in os.environ.get("SCRAPERS", "tripadvisor").split(",")
        if s.strip()
    )
    print(f"Running scrapers: {scrapers}")

    if "youtube" in scrapers:
        run_youtube(pois)
    if "google" in scrapers:
        run_google_reviews(pois)
    if "tripadvisor" in scrapers:
        run_tripadvisor(pois)


if __name__ == "__main__":
    main()
