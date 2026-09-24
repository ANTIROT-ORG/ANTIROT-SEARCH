"""
Convert humanknowledge.json → sources.json format and merge.
Maps 30 categories of human knowledge into crawler source entries.
"""

import json
import os

HK_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "humanknowledge.json")
SRC_PATH = os.path.join(os.path.dirname(__file__), "sources.json")

# Map humanknowledge categories → crawler categories
CATEGORY_MAP = {
    "Encyclopedias_and_Knowledge_Bases": "knowledge_foundations",
    "WIKIS_and_COLLABORATIVE_KNOWLEDGE": "knowledge_foundations",
    "Open_Access_Research_and_Scholarship": "science_research",
    "Open_Education_and_Free_Courses": "university_learning",
    "Digital_Libraries_and_Archives": "knowledge_foundations",
    "Encyclopedic_Reference_and_Special_Knowledge": "science_research",
    "QandA_and_Community_Knowledge": "programming_engineering",
    "Open_Source_and_Developer_Knowledge": "programming_engineering",
    "Science_and_Research": "science_research",
    "Medical_and_Health_Knowledge": "science_research",
    "Legal_Knowledge": "deep_thinking",
    "Financial_Literacy_and_Economics": "data_economics",
    "Food_and_Cooking": "knowledge_foundations",
    "DIY_Making_and_Crafts": "knowledge_foundations",
    "Home_and_Garden": "knowledge_foundations",
    "Music_and_Arts": "knowledge_foundations",
    "History_and_Culture": "deep_thinking",
    "Language_and_Linguistics": "deep_thinking",
    "Philosophy_and_Ethics": "deep_thinking",
    "Government_and_Public_Domain_Data": "data_economics",
    "Astronomy_and_Space": "science_research",
    "Citizen_Science_and_Crowdsourced_Knowledge": "science_research",
    "Outdoor_and_Survival": "knowledge_foundations",
    "Environment_and_Sustainability": "science_research",
    "Mathematics_and_Problem_Solving": "science_research",
    "Weather_and_Earth_Science": "science_research",
    "Sports_and_Physical_Knowledge": "knowledge_foundations",
    "Parenting_and_Family": "knowledge_foundations",
    "Business_and_Economics": "data_economics",
    "Psychology_and_Human_Behavior": "science_research",
    "Engineering_and_Technology": "programming_engineering",
    "Travel_and_Geography": "knowledge_foundations",
    "Religion_and_Spirituality": "deep_thinking",
    "Social_Science_and_Anthropology": "deep_thinking",
}

# Quality scores by source type
TYPE_QUALITY = {
    "crowdsourced_encyclopedia": 0.95,
    "expert_encyclopedia": 0.98,
    "expert_edited_encyclopedia": 0.96,
    "peer_reviewed_encyclopedia": 0.97,
    "community_repository": 0.88,
    "community_encyclopedia": 0.85,
    "open_wiki": 0.87,
    "wiki_dictionary": 0.90,
    "source_text_library": 0.92,
    "open_textbooks": 0.93,
    "structured_knowledge": 0.91,
    "how_to_wiki": 0.88,
    "critical_thinking_wiki": 0.86,
    "rationality_community": 0.89,
    "sustainability_wiki": 0.87,
    "wiki_directory": 0.85,
    "community_wiki": 0.84,
    "preprint_server": 0.96,
    "preprint_aggregator": 0.94,
    "academic_archive": 0.95,
    "oa_directory": 0.93,
    "oa_aggregator": 0.92,
    "academic_search": 0.91,
    "oa_repository": 0.93,
    "oa_journal": 0.94,
    "oa_publisher": 0.93,
    "open_courseware": 0.96,
    "free_education": 0.95,
    "mooc_platform": 0.92,
    "coding_education": 0.93,
    "coding_tutorials": 0.90,
    "oer_platform": 0.91,
    "digital_library": 0.95,
    "ebook_archive": 0.94,
    "audiobook_archive": 0.92,
    "book_search": 0.90,
    "classics_archive": 0.93,
    "cultural_archive": 0.94,
    "national_library": 0.96,
    "museum_archive": 0.94,
    "philosophy_reference": 0.97,
    "math_reference": 0.95,
    "math_history": 0.93,
    "biology_reference": 0.94,
    "chemistry_reference": 0.95,
    "physics_reference": 0.95,
    "electronics_reference": 0.92,
    "qa_technical": 0.91,
    "qa_network": 0.90,
    "qa_math": 0.93,
    "qa_science": 0.92,
    "qa_cooking": 0.88,
    "qa_gardening": 0.87,
    "qa_diy": 0.86,
    "qa_general": 0.85,
    "community_forum": 0.84,
    "dev_platform": 0.92,
    "web_docs": 0.96,
    "docs_aggregator": 0.93,
    "docs_hosting": 0.91,
    "cs_tutorials": 0.92,
    "programming_wiki": 0.90,
    "tech_community": 0.88,
    "tech_articles": 0.89,
    "linux_docs": 0.93,
    "linux_wiki": 0.92,
    "tech_docs": 0.91,
    "gov_science": 0.96,
    "research_institution": 0.97,
    "science_portal": 0.93,
    "science_journal": 0.96,
    "citizen_science": 0.91,
    "medical_reference": 0.97,
    "gov_health": 0.96,
    "intl_health": 0.95,
    "health_information": 0.92,
    "nutrition_reference": 0.91,
    "evidence_based_medicine": 0.96,
    "legal_reference": 0.95,
    "legal_database": 0.94,
    "gov_legal": 0.96,
    "legal_search": 0.92,
    "legal_information": 0.91,
    "legal_encyclopedia": 0.93,
    "finance_education": 0.90,
    "free_finance_education": 0.92,
    "personal_finance": 0.89,
    "nonprofit_finance": 0.91,
    "open_education": 0.90,
    "recipe_community": 0.87,
    "open_recipe_community": 0.88,
    "community_recipes": 0.86,
    "food_journalism": 0.90,
    "food_science": 0.91,
    "chef_recipes": 0.89,
    "cuisine_recipes": 0.88,
    "diy_community": 0.88,
    "woodworking_community": 0.87,
    "fiber_arts_community": 0.86,
    "3d_printing_community": 0.88,
    "maker_magazine": 0.89,
    "home_diy_community": 0.87,
    "gardening_community": 0.88,
    "permaculture_community": 0.89,
    "permaculture_wiki": 0.90,
    "citizen_science_nature": 0.91,
    "plant_identification": 0.90,
    "traditional_knowledge": 0.88,
    "how_to": 0.87,
    "home_improvement": 0.88,
    "music_scores": 0.94,
    "choral_music": 0.92,
    "open_art_collection": 0.93,
    "art_aggregator": 0.91,
    "media_repository": 0.92,
    "museum_open_access": 0.94,
    "sheet_music_community": 0.90,
    "music_education": 0.91,
    "art_database": 0.90,
    "cultural_heritage": 0.94,
    "intangible_heritage": 0.93,
    "folklore_database": 0.92,
    "indigenous_knowledge": 0.91,
    "american_folklore": 0.90,
    "fairy_tales": 0.89,
    "history_encyclopedia": 0.93,
    "wiki_dictionary": 0.90,
    "language_courses": 0.91,
    "pronunciation_dictionary": 0.90,
    "open_dictionary": 0.89,
    "language_reference": 0.92,
    "writing_systems": 0.91,
    "language_education": 0.90,
    "language_community": 0.88,
    "philosophy_index": 0.94,
    "philosophy_archive": 0.93,
    "philosophy_news": 0.91,
    "gov_data": 0.93,
    "science_data": 0.92,
    "public_interest": 0.91,
    "civic_knowledge": 0.90,
    "civic_data": 0.89,
    "gov_reference": 0.92,
    "astronomy_daily": 0.93,
    "astronomy_magazine": 0.92,
    "astronomy_education": 0.91,
    "star_map": 0.90,
    "citizen_astronomy": 0.89,
    "professional_astronomy": 0.94,
    "citizen_science_platform": 0.91,
    "citizen_science_birding": 0.92,
    "crowdsourced_maps": 0.93,
    "citizen_science_protein": 0.91,
    "citizen_science_astronomy": 0.90,
    "citizen_reporting": 0.89,
    "travel_knowledge": 0.90,
    "survival_community": 0.88,
    "outdoor_community": 0.87,
    "outdoor_education": 0.89,
    "outdoor_skills": 0.88,
    "climbing_knowledge": 0.87,
    "trail_knowledge": 0.86,
    "gov_environment": 0.94,
    "environmental_data": 0.92,
    "conservation_data": 0.93,
    "agriculture_knowledge": 0.91,
    "data_research": 0.93,
    "math_wiki": 0.94,
    "math_qa": 0.93,
    "math_community": 0.92,
    "math_database": 0.94,
    "math_encyclopedia": 0.93,
    "math_interactive": 0.91,
    "weather_data": 0.92,
    "weather_community": 0.90,
    "seismology": 0.93,
    "earth_science": 0.92,
    "open_weather_data": 0.91,
    "weather_visualization": 0.90,
    "exercise_reference": 0.90,
    "exercise_science": 0.91,
    "sports_education": 0.89,
    "running_knowledge": 0.88,
    "strength_training": 0.89,
    "parenting_reference": 0.91,
    "pregnancy_parenting": 0.90,
    "child_development": 0.92,
    "science_parenting": 0.91,
    "business_review": 0.93,
    "economic_data": 0.94,
    "development_data": 0.93,
    "global_data": 0.94,
    "free_economics": 0.92,
    "psychology_magazine": 0.90,
    "professional_psychology": 0.93,
    "mental_health": 0.91,
    "engineering_education": 0.92,
    "electronics_community": 0.90,
    "engineering_reference": 0.91,
    "hardware_community": 0.89,
    "maker_community": 0.88,
    "travel_wiki": 0.91,
    "travel_guides": 0.90,
    "curated_atlas": 0.91,
    "geographic_tool": 0.92,
    "geography_magazine": 0.91,
    "geographic_database": 0.93,
    "scripture_reference": 0.92,
    "sacred_texts": 0.91,
    "religion_archive": 0.93,
    "philosophy_religion": 0.92,
    "interfaith_knowledge": 0.90,
    "social_research": 0.93,
    "social_data": 0.94,
    "anthropology_research": 0.92,
    "anthropology_magazine": 0.91,
}


def convert_and_merge():
    """Read humanknowledge.json, convert to sources format, merge with sources.json"""
    # Load existing sources
    with open(SRC_PATH) as f:
        existing = json.load(f)
    existing_names = {s["name"] for s in existing["sources"]}

    # Load humanknowledge.json
    with open(HK_PATH) as f:
        hk = json.load(f)

    new_sources = []
    for cat_name, cat_data in hk.get("categories", {}).items():
        crawler_cat = CATEGORY_MAP.get(cat_name, "knowledge_foundations")
        for site in cat_data.get("websites", []):
            name = site["name"]
            url = site["url"].rstrip("/")
            if name in existing_names:
                continue
            if not url.startswith("http"):
                continue

            source_type = site.get("type", "webpage")
            quality = TYPE_QUALITY.get(source_type, 0.85)

            new_sources.append({
                "name": name,
                "url": url,
                "category": crawler_cat,
                "crawl_type": "html",
                "quality_score": quality,
                "update_frequency": "weekly",
                "has_api": False,
                "api_endpoint": None,
                "rate_limit": 2.0,
                "priority": 2,
            })
            existing_names.add(name)

    # Merge
    existing["sources"].extend(new_sources)

    # Add new category if needed
    for cat in CATEGORY_MAP.values():
        if cat not in existing.get("categories", []):
            existing.setdefault("categories", []).append(cat)

    # Write back
    with open(SRC_PATH, "w") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)

    print(f"Added {len(new_sources)} new sources from humanknowledge.json")
    print(f"Total sources: {len(existing['sources'])}")
    print(f"Categories: {len(existing.get('categories', []))}")

    # Print breakdown
    by_cat = {}
    for s in new_sources:
        by_cat[s["category"]] = by_cat.get(s["category"], 0) + 1
    for cat, count in sorted(by_cat.items(), key=lambda x: -x[1]):
        print(f"  {cat}: +{count}")


if __name__ == "__main__":
    convert_and_merge()
