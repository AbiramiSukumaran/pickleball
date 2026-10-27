"""
Project Pioneer Evolution: Automated Post-Match Analytics & AI Drill Generator
Powered by Spanner Omni GraphRAG (GQL) + Gemini
"""
import os
from google.cloud import spanner
import google.generativeai as genai

def fetch_weakness_patterns(database):
    """Queries Spanner Omni Graph using GQL to identify recurring unforced error sequences."""
    gql_query = """
    GRAPH PickleballGraph
    MATCH (p:Player)-[s:SHOT]->(z:CourtZone)
    WHERE s.outcome = 'UNFORCED_ERROR'
    RETURN p.name AS player_name, s.shot_type AS shot_type, z.zone_name AS target_zone, COUNT(*) AS error_count
    GROUP BY player_name, shot_type, target_zone
    ORDER BY error_count DESC
    LIMIT 5
    """
    weaknesses = []
    with database.snapshot() as snapshot:
        results = snapshot.execute_sql(gql_query)
        for row in results:
            weaknesses.append({
                "player": row[0],
                "shot_type": row[1],
                "zone": row[2],
                "errors": row[3]
            })
    return weaknesses

def generate_personalized_drills(weaknesses):
    """Uses Gemini to synthesize a 15-minute court practice routine from GraphRAG insights."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if api_key:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-flash")
        prompt = (
            f"Based on these Spanner Omni GraphRAG player shot weakness patterns: {weaknesses}, "
            "design a structured 15-minute on-court Pickleball training drill routine focusing on "
            "kitchen-line control, third-shot drop consistency, and footwork recovery."
        )
        response = model.generate_content(prompt)
        return response.text
    return "Offline Drill Prescription: 10 mins Kitchen Dink Cross-Court Control + 5 mins 3rd-Shot Drop Reset."

if __name__ == "__main__":
    sample_graph_insights = [
        {"player": "Player 1", "shot_type": "Third-Shot Drop", "zone": "Kitchen Left", "errors": 7},
        {"player": "Player 1", "shot_type": "Backhand Drive", "zone": "Baseline Deep", "errors": 4}
    ]
    print("=== Spanner Omni GraphRAG Drill Prescription ===")
    print(generate_personalized_drills(sample_graph_insights))
