import os
import time
import json
import random
import uuid
import datetime
import threading
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# ==========================================
# Spanner Omni Configuration
# ==========================================
SPANNER_EMULATOR_HOST = os.getenv("SPANNER_EMULATOR_HOST", "localhost:9010")
SPANNER_PROJECT = os.getenv("SPANNER_PROJECT", "omni-demo")
SPANNER_INSTANCE = os.getenv("SPANNER_INSTANCE", "omni-instance")
SPANNER_DATABASE = os.getenv("SPANNER_DATABASE", "pickleball-db")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

if SPANNER_EMULATOR_HOST:
    os.environ["SPANNER_EMULATOR_HOST"] = SPANNER_EMULATOR_HOST

# Initialize Gemini Client
ai_client = None
if GEMINI_API_KEY and GEMINI_API_KEY != "your-gemini-api-key":
    try:
        from google import genai
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception:
        try:
            import google.generativeai as legacy_genai
            legacy_genai.configure(api_key=GEMINI_API_KEY)
            ai_client = legacy_genai.GenerativeModel("gemini-3.8-flash")
        except Exception as e2:
            print(f"Warning initializing Gemini client: {e2}")

# Spanner Client & Database State
spanner_client = None
spanner_db = None
spanner_connected = False
spanner_error = None
db_initialized = False

# Complete Table Definitions
TABLE_SCHEMAS = {
    "Courts": """CREATE TABLE Courts (
        court_id INT64 NOT NULL,
        name STRING(100) NOT NULL,
        location STRING(100) NOT NULL,
        surface_type STRING(50) NOT NULL,
        is_indoor BOOL NOT NULL,
        hourly_rate FLOAT64 NOT NULL,
        has_lighting BOOL NOT NULL,
        created_at TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp=true)
    ) PRIMARY KEY (court_id)""",

    "Players": """CREATE TABLE Players (
        player_id INT64 NOT NULL,
        name STRING(100) NOT NULL,
        email STRING(100) NOT NULL,
        skill_level STRING(20) NOT NULL,
        phone STRING(50),
        created_at TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp=true)
    ) PRIMARY KEY (player_id)""",

    "Bookings": """CREATE TABLE Bookings (
        booking_id INT64 NOT NULL,
        court_id INT64 NOT NULL,
        player_id INT64 NOT NULL,
        booking_date DATE NOT NULL,
        start_hour INT64 NOT NULL,
        end_hour INT64 NOT NULL,
        player_name STRING(100) NOT NULL,
        court_name STRING(100) NOT NULL,
        status STRING(20) NOT NULL,
        notes STRING(MAX),
        created_at TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp=true)
    ) PRIMARY KEY (booking_id)""",

    "MatchRequests": """CREATE TABLE MatchRequests (
        match_id INT64 NOT NULL,
        player_id INT64 NOT NULL,
        player_name STRING(100) NOT NULL,
        court_id INT64,
        court_name STRING(100),
        play_date DATE NOT NULL,
        start_hour INT64 NOT NULL,
        skill_required STRING(20) NOT NULL,
        match_type STRING(20) NOT NULL,
        spots_open INT64 NOT NULL,
        status STRING(20) NOT NULL,
        notes STRING(MAX),
        created_at TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp=true)
    ) PRIMARY KEY (match_id)""",

    "CourtZones": """CREATE TABLE CourtZones (
        zone_id STRING(50) NOT NULL,
        name STRING(100) NOT NULL,
        is_kitchen BOOL NOT NULL,
        side STRING(50) NOT NULL
    ) PRIMARY KEY (zone_id)""",

    "Shots": """CREATE TABLE Shots (
        shot_id STRING(50) NOT NULL,
        player_id STRING(50) NOT NULL,
        shot_type STRING(100) NOT NULL,
        speed_mph FLOAT64,
        spin_type STRING(50),
        outcome STRING(50),
        from_zone_id STRING(50),
        to_zone_id STRING(50),
        created_at TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp=true)
    ) PRIMARY KEY (shot_id)""",

    "ShotTransitions": """CREATE TABLE ShotTransitions (
        transition_id STRING(50) NOT NULL,
        from_shot_id STRING(50) NOT NULL,
        to_shot_id STRING(50) NOT NULL,
        rally_depth INT64,
        pressure_level STRING(20)
    ) PRIMARY KEY (transition_id)""",

    "GameSessions": """CREATE TABLE GameSessions (
        session_id STRING(50) NOT NULL,
        player_id INT64 NOT NULL,
        player_score INT64 NOT NULL,
        bot_score INT64 NOT NULL,
        bot_difficulty STRING(20) NOT NULL,
        winner STRING(20) NOT NULL,
        rally_length INT64 NOT NULL,
        fault_type STRING(100),
        coaching_summary STRING(MAX),
        created_at TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp=true)
    ) PRIMARY KEY (session_id)"""
}


def get_spanner_db():
    global spanner_client, spanner_db, spanner_connected, spanner_error
    if spanner_db is not None:
        return spanner_db
    try:
        from google.cloud import spanner
        if spanner_client is None:
            spanner_client = spanner.Client(project=SPANNER_PROJECT)
        instance = spanner_client.instance(SPANNER_INSTANCE)
        database = instance.database(SPANNER_DATABASE)
        
        # Test connection
        with database.snapshot() as snapshot:
            list(snapshot.execute_sql("SELECT 1"))
            
        spanner_db = database
        spanner_connected = True
        spanner_error = None
        return spanner_db
    except Exception as e:
        spanner_connected = False
        spanner_error = str(e)
        return None


def init_database_schema_and_seed():
    """Initializes Spanner Omni instance, schema tables, and demo seed data."""
    global spanner_client, spanner_db, spanner_connected, spanner_error, db_initialized
    if db_initialized:
        return True, "Already initialized."
    try:
        from google.cloud import spanner
        if spanner_client is None:
            spanner_client = spanner.Client(project=SPANNER_PROJECT)

        # 1. Ensure Instance Exists
        instance = spanner_client.instance(SPANNER_INSTANCE)
        try:
            if not instance.exists():
                print(f"Creating Spanner instance '{SPANNER_INSTANCE}'...")
                config_name = f"projects/{SPANNER_PROJECT}/instanceConfigs/emulator-config"
                instance.configuration_name = config_name
                instance.display_name = "Spanner Omni Pickleball Instance"
                instance.node_count = 1
                op = instance.create()
                op.result(15)
        except Exception as e:
            print(f"Instance check note: {e}")

        # 2. Ensure Database Exists & Apply Schema
        database = instance.database(SPANNER_DATABASE)
        all_ddls = list(TABLE_SCHEMAS.values())

        if not database.exists():
            print(f"Creating Spanner database '{SPANNER_DATABASE}' with schema...")
            op = database.create(ddl_statements=all_ddls)
            op.result(30)
            print("Spanner Omni Pickleball schema created.")
        else:
            # Check for missing tables
            existing_tables = set()
            try:
                with database.snapshot() as snapshot:
                    results = snapshot.execute_sql(
                        "SELECT table_name FROM information_schema.tables WHERE table_schema = ''"
                    )
                    for row in results:
                        existing_tables.add(row[0].lower())
            except Exception as e:
                print(f"Info schema query note: {e}")

            missing_ddls = [
                ddl for tname, ddl in TABLE_SCHEMAS.items()
                if tname.lower() not in existing_tables
            ]

            if missing_ddls:
                print("Updating DDL for missing tables...")
                for ddl in missing_ddls:
                    try:
                        op = database.update_ddl([ddl])
                        op.result(10)
                    except Exception:
                        pass

        spanner_db = database
        spanner_connected = True
        spanner_error = None
        db_initialized = True

        # 3. Seed initial data
        seed_sample_data(database)
        return True, "Spanner Omni initialized successfully."
    except Exception as e:
        spanner_connected = False
        spanner_error = str(e)
        print(f"Database init notice: {e}")
        return False, str(e)


def seed_sample_data(database):
    """Seed initial courts, players, bookings, match requests, and zones."""
    try:
        from google.cloud import spanner
        with database.snapshot() as snapshot:
            results = list(snapshot.execute_sql("SELECT COUNT(*) FROM Courts"))
            count = results[0][0] if results else 0
            if count > 0:
                return

        print("Seeding initial court and game telemetry into Spanner Omni...")
        today = datetime.date.today().isoformat()
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()

        def insert_data(transaction):
            # Courts
            courts = [
                (1, "Center Championship Court", "Omni Arena 1", "Pro-Cushion Acrylic", True, 45.0, True, spanner.COMMIT_TIMESTAMP),
                (2, "Grandstand Court 2", "Omni Outdoor Park", "Standard Hardcourt", False, 30.0, True, spanner.COMMIT_TIMESTAMP),
                (3, "Training Pavilion Court 3", "Omni Club West", "Gel-Foam Indoor", True, 35.0, True, spanner.COMMIT_TIMESTAMP),
                (4, "Sunset Patio Court 4", "Omni Outdoor Park", "Standard Hardcourt", False, 25.0, True, spanner.COMMIT_TIMESTAMP),
            ]
            transaction.insert(
                table="Courts",
                columns=["court_id", "name", "location", "surface_type", "is_indoor", "hourly_rate", "has_lighting", "created_at"],
                values=courts
            )

            # Players
            players = [
                (101, "You (Challenger)", "challenger@example.com", "4.5 (Advanced)", "555-0101", spanner.COMMIT_TIMESTAMP),
                (102, "OmniBot-3000", "omnibot@spanner.google", "5.0 (DUPR Master)", "555-0102", spanner.COMMIT_TIMESTAMP),
                (103, "Sarah Jenkins", "sarah.j@example.com", "4.0 (Intermediate)", "555-0103", spanner.COMMIT_TIMESTAMP),
                (104, "Marcus Rodriguez", "marcus.r@example.com", "4.5 (Expert)", "555-0104", spanner.COMMIT_TIMESTAMP),
            ]
            transaction.insert(
                table="Players",
                columns=["player_id", "name", "email", "skill_level", "phone", "created_at"],
                values=players
            )

            # Bookings
            bookings = [
                (1001, 1, 101, today, 10, 11, "You (Challenger)", "Center Championship Court", "CONFIRMED", "DUPR practice", spanner.COMMIT_TIMESTAMP),
                (1002, 3, 103, today, 17, 19, "Sarah Jenkins", "Training Pavilion Court 3", "CONFIRMED", "Evening doubles", spanner.COMMIT_TIMESTAMP),
            ]
            transaction.insert(
                table="Bookings",
                columns=["booking_id", "court_id", "player_id", "booking_date", "start_hour", "end_hour", "player_name", "court_name", "status", "notes", "created_at"],
                values=bookings
            )

            # Match Requests
            matches = [
                (2001, 101, "You (Challenger)", 1, "Center Championship Court", tomorrow, 9, "4.0 - 4.5", "Doubles", 2, "OPEN", "Morning competitive doubles.", spanner.COMMIT_TIMESTAMP),
                (2002, 104, "Marcus Rodriguez", 2, "Grandstand Court 2", today, 18, "4.5", "Singles", 1, "OPEN", "High-intensity singles drill.", spanner.COMMIT_TIMESTAMP),
            ]
            transaction.insert(
                table="MatchRequests",
                columns=["match_id", "player_id", "player_name", "court_id", "court_name", "play_date", "start_hour", "skill_required", "match_type", "spots_open", "status", "notes", "created_at"],
                values=matches
            )

            # Court Zones
            zones = [
                ("z_kitchen_left", "Left Kitchen (NVZ)", True, "NVZ"),
                ("z_kitchen_right", "Right Kitchen (NVZ)", True, "NVZ"),
                ("z_mid_left", "Mid-Court Left", False, "Transition"),
                ("z_mid_right", "Mid-Court Right", False, "Transition"),
                ("z_baseline_left", "Baseline Left (Backhand)", False, "Baseline"),
                ("z_baseline_right", "Baseline Right (Forehand)", False, "Baseline"),
            ]
            for zid, name, is_k, side in zones:
                transaction.execute_update(
                    "INSERT OR IGNORE INTO CourtZones (zone_id, name, is_kitchen, side) "
                    "VALUES (@zid, @name, @is_k, @side)",
                    params={"zid": zid, "name": name, "is_k": is_k, "side": side},
                    param_types={
                        "zid": spanner.param_types.STRING, "name": spanner.param_types.STRING,
                        "is_k": spanner.param_types.BOOL, "side": spanner.param_types.STRING
                    }
                )

        database.run_in_transaction(insert_data)
        print("✅ Demo data seeded successfully.")
    except Exception as e:
        print(f"Seed data note: {e}")


# Mock Fallback Data
mock_courts = [
    {"court_id": 1, "name": "Center Championship Court", "location": "Omni Arena 1", "surface_type": "Pro-Cushion Acrylic", "is_indoor": True, "hourly_rate": 45.0, "has_lighting": True},
    {"court_id": 2, "name": "Grandstand Court 2", "location": "Omni Outdoor Park", "surface_type": "Standard Hardcourt", "is_indoor": False, "hourly_rate": 30.0, "has_lighting": True},
    {"court_id": 3, "name": "Training Pavilion Court 3", "location": "Omni Club West", "surface_type": "Gel-Foam Indoor", "is_indoor": True, "hourly_rate": 35.0, "has_lighting": True},
    {"court_id": 4, "name": "Sunset Patio Court 4", "location": "Omni Outdoor Park", "surface_type": "Standard Hardcourt", "is_indoor": False, "hourly_rate": 25.0, "has_lighting": True},
]

mock_bookings = [
    {"booking_id": 1001, "court_id": 1, "player_id": 101, "booking_date": datetime.date.today().isoformat(), "start_hour": 10, "end_hour": 11, "player_name": "You (Challenger)", "court_name": "Center Championship Court", "status": "CONFIRMED", "notes": "DUPR Championship match practice"},
    {"booking_id": 1002, "court_id": 3, "player_id": 103, "booking_date": datetime.date.today().isoformat(), "start_hour": 17, "end_hour": 19, "player_name": "Sarah Jenkins", "court_name": "Training Pavilion Court 3", "status": "CONFIRMED", "notes": "Evening friendly doubles"},
]

mock_matches = [
    {"match_id": 2001, "player_id": 101, "player_name": "You (Challenger)", "court_id": 1, "court_name": "Center Championship Court", "play_date": (datetime.date.today() + datetime.timedelta(days=1)).isoformat(), "start_hour": 9, "skill_required": "4.0 - 4.5", "match_type": "Doubles", "spots_open": 2, "status": "OPEN", "notes": "Looking for 2 solid players for Saturday morning competitive doubles."},
    {"match_id": 2002, "player_id": 104, "player_name": "Marcus Rodriguez", "court_id": 2, "court_name": "Grandstand Court 2", "play_date": datetime.date.today().isoformat(), "start_hour": 18, "skill_required": "4.5", "match_type": "Singles", "spots_open": 1, "status": "OPEN", "notes": "High-intensity singles drill and dinking resets."},
]

mock_game_sessions = []


# ==========================================
# Routes & API Endpoints
# ==========================================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/favicon.ico")
def favicon():
    return "", 204

@app.route("/api/status", methods=["GET"])
def api_status():
    db = get_spanner_db()
    court_count = 0
    booking_count = 0
    match_count = 0
    shot_count = 0

    if db:
        try:
            with db.snapshot() as snapshot:
                try:
                    r1 = list(snapshot.execute_sql("SELECT COUNT(*) FROM Courts"))
                    court_count = r1[0][0] if r1 else 0
                except Exception:
                    court_count = len(mock_courts)

                try:
                    r2 = list(snapshot.execute_sql("SELECT COUNT(*) FROM Bookings"))
                    booking_count = r2[0][0] if r2 else 0
                except Exception:
                    booking_count = len(mock_bookings)

                try:
                    r3 = list(snapshot.execute_sql("SELECT COUNT(*) FROM MatchRequests"))
                    match_count = r3[0][0] if r3 else 0
                except Exception:
                    match_count = len(mock_matches)

                try:
                    r4 = list(snapshot.execute_sql("SELECT COUNT(*) FROM Shots"))
                    shot_count = r4[0][0] if r4 else 0
                except Exception:
                    shot_count = 6
        except Exception:
            court_count = len(mock_courts)
            booking_count = len(mock_bookings)
            match_count = len(mock_matches)
            shot_count = 6
    else:
        court_count = len(mock_courts)
        booking_count = len(mock_bookings)
        match_count = len(mock_matches)
        shot_count = 6

    return jsonify({
        "spanner_connected": spanner_connected,
        "spanner_error": spanner_error,
        "emulator_host": SPANNER_EMULATOR_HOST,
        "project": SPANNER_PROJECT,
        "instance": SPANNER_INSTANCE,
        "database": SPANNER_DATABASE,
        "counts": {
            "courts": court_count,
            "bookings": booking_count,
            "matches": match_count,
            "shots": shot_count
        },
        "gemini_configured": bool(GEMINI_API_KEY)
    })

@app.route("/api/spanner/init", methods=["POST"])
def api_spanner_init():
    success, msg = init_database_schema_and_seed()
    return jsonify({"success": success, "message": msg})

@app.route("/api/courts", methods=["GET", "POST"])
def api_courts():
    db = get_spanner_db()
    if request.method == "POST":
        data = request.json or {}
        court_id = int(data.get("court_id") or int(time.time() * 1000) % 1000000)
        name = data.get("name", "New Court")
        location = data.get("location", "Main Complex")
        surface_type = data.get("surface_type", "Standard Acrylic")
        is_indoor = bool(data.get("is_indoor", True))
        hourly_rate = float(data.get("hourly_rate", 30.0))
        has_lighting = bool(data.get("has_lighting", True))

        if db:
            from google.cloud import spanner
            def insert_court(transaction):
                transaction.insert(
                    table="Courts",
                    columns=["court_id", "name", "location", "surface_type", "is_indoor", "hourly_rate", "has_lighting", "created_at"],
                    values=[(court_id, name, location, surface_type, is_indoor, hourly_rate, has_lighting, spanner.COMMIT_TIMESTAMP)]
                )
            db.run_in_transaction(insert_court)
        else:
            mock_courts.append({
                "court_id": court_id,
                "name": name,
                "location": location,
                "surface_type": surface_type,
                "is_indoor": is_indoor,
                "hourly_rate": hourly_rate,
                "has_lighting": has_lighting
            })
        return jsonify({"success": True, "court_id": court_id})

    # GET courts
    if db:
        courts = []
        try:
            with db.snapshot() as snapshot:
                results = snapshot.execute_sql("SELECT court_id, name, location, surface_type, is_indoor, hourly_rate, has_lighting FROM Courts ORDER BY court_id ASC")
                for row in results:
                    courts.append({
                        "court_id": row[0],
                        "name": row[1],
                        "location": row[2],
                        "surface_type": row[3],
                        "is_indoor": row[4],
                        "hourly_rate": row[5],
                        "has_lighting": row[6]
                    })
            return jsonify(courts)
        except Exception:
            return jsonify(mock_courts)
    return jsonify(mock_courts)

@app.route("/api/bookings", methods=["GET", "POST"])
def api_bookings():
    db = get_spanner_db()
    if request.method == "POST":
        data = request.json or {}
        booking_id = int(data.get("booking_id") or int(time.time() * 1000) % 10000000)
        court_id = int(data.get("court_id", 1))
        player_id = int(data.get("player_id", 101))
        booking_date = str(data.get("booking_date", datetime.date.today().isoformat()))
        start_hour = int(data.get("start_hour", 9))
        end_hour = int(data.get("end_hour", start_hour + 1))
        player_name = str(data.get("player_name", "Player"))
        court_name = str(data.get("court_name", "Court"))
        notes = str(data.get("notes", ""))

        if db:
            from google.cloud import spanner
            def make_reservation_tx(transaction):
                query = """
                SELECT booking_id FROM Bookings
                WHERE court_id = @court_id 
                  AND booking_date = @booking_date 
                  AND status = 'CONFIRMED'
                  AND ((start_hour <= @start_hour AND end_hour > @start_hour)
                       OR (start_hour < @end_hour AND end_hour >= @end_hour)
                       OR (start_hour >= @start_hour AND end_hour <= @end_hour))
                """
                params = {
                    "court_id": court_id,
                    "booking_date": booking_date,
                    "start_hour": start_hour,
                    "end_hour": end_hour,
                }
                param_types = {
                    "court_id": spanner.param_types.INT64,
                    "booking_date": spanner.param_types.DATE,
                    "start_hour": spanner.param_types.INT64,
                    "end_hour": spanner.param_types.INT64,
                }
                conflicts = list(transaction.execute_sql(query, params=params, param_types=param_types))
                if len(conflicts) > 0:
                    raise ValueError("Conflict: Court is already reserved for this date and time slot.")

                transaction.insert(
                    table="Bookings",
                    columns=["booking_id", "court_id", "player_id", "booking_date", "start_hour", "end_hour", "player_name", "court_name", "status", "notes", "created_at"],
                    values=[(booking_id, court_id, player_id, booking_date, start_hour, end_hour, player_name, court_name, "CONFIRMED", notes, spanner.COMMIT_TIMESTAMP)]
                )

            try:
                db.run_in_transaction(make_reservation_tx)
                return jsonify({"success": True, "booking_id": booking_id, "message": "Reservation confirmed in Spanner!"})
            except ValueError as ve:
                return jsonify({"success": False, "error": str(ve)}), 409
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500
        else:
            mock_bookings.append({
                "booking_id": booking_id,
                "court_id": court_id,
                "player_id": player_id,
                "booking_date": booking_date,
                "start_hour": start_hour,
                "end_hour": end_hour,
                "player_name": player_name,
                "court_name": court_name,
                "status": "CONFIRMED",
                "notes": notes
            })
            return jsonify({"success": True, "booking_id": booking_id, "message": "Reservation confirmed (Mock Mode)!"})

    # GET bookings
    if db:
        bookings = []
        try:
            with db.snapshot() as snapshot:
                results = snapshot.execute_sql("""
                    SELECT booking_id, court_id, player_id, booking_date, start_hour, end_hour, player_name, court_name, status, notes
                    FROM Bookings
                    ORDER BY booking_date DESC, start_hour DESC
                """)
                for row in results:
                    bookings.append({
                        "booking_id": row[0],
                        "court_id": row[1],
                        "player_id": row[2],
                        "booking_date": str(row[3]),
                        "start_hour": row[4],
                        "end_hour": row[5],
                        "player_name": row[6],
                        "court_name": row[7],
                        "status": row[8],
                        "notes": row[9] or ""
                    })
            return jsonify(bookings)
        except Exception:
            return jsonify(mock_bookings)
    return jsonify(mock_bookings)

@app.route("/api/bookings/<int:booking_id>", methods=["DELETE"])
def api_cancel_booking(booking_id):
    db = get_spanner_db()
    if db:
        try:
            from google.cloud import spanner
            def cancel_tx(transaction):
                transaction.execute_update(
                    "UPDATE Bookings SET status = 'CANCELLED' WHERE booking_id = @bid",
                    params={"bid": booking_id},
                    param_types={"bid": spanner.param_types.INT64}
                )
            db.run_in_transaction(cancel_tx)
            return jsonify({"success": True, "message": "Booking cancelled in Spanner."})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
    else:
        for b in mock_bookings:
            if b["booking_id"] == booking_id:
                b["status"] = "CANCELLED"
                return jsonify({"success": True, "message": "Booking cancelled."})
        return jsonify({"success": False, "error": "Booking not found"}), 404

@app.route("/api/matches", methods=["GET", "POST"])
def api_matches():
    db = get_spanner_db()
    if request.method == "POST":
        data = request.json or {}
        match_id = int(data.get("match_id") or int(time.time() * 1000) % 10000000)
        player_id = int(data.get("player_id", 101))
        player_name = str(data.get("player_name", "You (Challenger)"))
        court_id = int(data.get("court_id", 1))
        court_name = str(data.get("court_name", "Center Championship Court"))
        play_date = str(data.get("play_date", datetime.date.today().isoformat()))
        start_hour = int(data.get("start_hour", 10))
        skill_required = str(data.get("skill_required", "4.0 - 4.5"))
        match_type = str(data.get("match_type", "Doubles"))
        spots_open = int(data.get("spots_open", 2))
        notes = str(data.get("notes", ""))

        if db:
            from google.cloud import spanner
            def insert_match(transaction):
                transaction.insert(
                    table="MatchRequests",
                    columns=["match_id", "player_id", "player_name", "court_id", "court_name", "play_date", "start_hour", "skill_required", "match_type", "spots_open", "status", "notes", "created_at"],
                    values=[(match_id, player_id, player_name, court_id, court_name, play_date, start_hour, skill_required, match_type, spots_open, "OPEN", notes, spanner.COMMIT_TIMESTAMP)]
                )
            db.run_in_transaction(insert_match)
        else:
            mock_matches.append({
                "match_id": match_id,
                "player_id": player_id,
                "player_name": player_name,
                "court_id": court_id,
                "court_name": court_name,
                "play_date": play_date,
                "start_hour": start_hour,
                "skill_required": skill_required,
                "match_type": match_type,
                "spots_open": spots_open,
                "status": "OPEN",
                "notes": notes
            })
        return jsonify({"success": True, "match_id": match_id})

    # GET matches
    if db:
        matches = []
        try:
            with db.snapshot() as snapshot:
                results = snapshot.execute_sql("""
                    SELECT match_id, player_id, player_name, court_id, court_name, play_date, start_hour, skill_required, match_type, spots_open, status, notes
                    FROM MatchRequests
                    WHERE status = 'OPEN'
                    ORDER BY play_date ASC, start_hour ASC
                """)
                for row in results:
                    matches.append({
                        "match_id": row[0],
                        "player_id": row[1],
                        "player_name": row[2],
                        "court_id": row[3],
                        "court_name": row[4],
                        "play_date": str(row[5]),
                        "start_hour": row[6],
                        "skill_required": row[7],
                        "match_type": row[8],
                        "spots_open": row[9],
                        "status": row[10],
                        "notes": row[11] or ""
                    })
            return jsonify(matches)
        except Exception:
            return jsonify(mock_matches)
    return jsonify(mock_matches)

@app.route("/api/matches/<int:match_id>/join", methods=["POST"])
def api_join_match(match_id):
    db = get_spanner_db()
    if db:
        try:
            from google.cloud import spanner
            def join_tx(transaction):
                row = list(transaction.execute_sql(
                    "SELECT spots_open FROM MatchRequests WHERE match_id = @mid AND status = 'OPEN'",
                    params={"mid": match_id},
                    param_types={"mid": spanner.param_types.INT64}
                ))
                if not row or row[0][0] <= 0:
                    raise ValueError("This match is already full or closed!")
                new_spots = row[0][0] - 1
                new_status = 'FILLED' if new_spots == 0 else 'OPEN'
                transaction.execute_update(
                    "UPDATE MatchRequests SET spots_open = @spots, status = @status WHERE match_id = @mid",
                    params={"spots": new_spots, "status": new_status, "mid": match_id},
                    param_types={"spots": spanner.param_types.INT64, "status": spanner.param_types.STRING, "mid": spanner.param_types.INT64}
                )

            db.run_in_transaction(join_tx)
            return jsonify({"success": True, "message": "Successfully joined the match!"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 400
    else:
        for m in mock_matches:
            if m["match_id"] == match_id and m["status"] == "OPEN":
                m["spots_open"] -= 1
                if m["spots_open"] <= 0:
                    m["status"] = "FILLED"
                return jsonify({"success": True, "message": "Successfully joined match (Mock Mode)!"})
        return jsonify({"success": False, "error": "Match not found or full"}), 400

@app.route("/api/ai/chat", methods=["POST"])
def api_ai_chat():
    data = request.json or {}
    user_prompt = data.get("prompt", "").strip()
    if not user_prompt:
        return jsonify({"error": "Prompt is required"}), 400

    db = get_spanner_db()
    courts_context = []
    bookings_context = []
    sessions_context = []
    shots_context = []
    try:
        if db:
            with db.snapshot() as snapshot:
                for c in snapshot.execute_sql("SELECT court_id, name, surface_type, is_indoor, hourly_rate FROM Courts"):
                    courts_context.append(f"Court {c[0]}: {c[1]} ({'Indoor' if c[3] else 'Outdoor'}, {c[2]}, ${c[4]}/hr)")
                for b in snapshot.execute_sql("SELECT court_name, booking_date, start_hour, end_hour, player_name FROM Bookings WHERE status = 'CONFIRMED'"):
                    bookings_context.append(f"Reserved: {b[0]} on {b[1]} from {b[2]}:00 to {b[3]}:00 by {b[4]}")
                try:
                    for s in snapshot.execute_sql("""
                        SELECT session_id, player_score, bot_score, bot_difficulty, winner, rally_length, fault_type, coaching_summary, created_at 
                        FROM GameSessions 
                        ORDER BY created_at DESC 
                        LIMIT 8
                    """):
                        sessions_context.append(
                            f"Match {s[0]}: Player {s[1]} - OmniBot {s[2]} (Bot DUPR: {s[3]}, Winner: {s[4]}, Rally Length: {s[5]} shots, Concluding Fault: {s[6]}). Tactical Note: {s[7][:120]}..."
                        )
                except Exception as ex_sess:
                    print(f"Session grounding note: {ex_sess}")
                try:
                    for st in snapshot.execute_sql("""
                        SELECT shot_type, outcome, count(*) as cnt 
                        FROM Shots 
                        GROUP BY shot_type, outcome 
                        LIMIT 10
                    """):
                        shots_context.append(f"{st[0]} ({st[1]}): {st[2]} times")
                except Exception as ex_shots:
                    print(f"Shot grounding note: {ex_shots}")
        else:
            courts_context = [f"Court {c['court_id']}: {c['name']} (${c['hourly_rate']}/hr)" for c in mock_courts]
            bookings_context = [f"Reserved: {b['court_name']} on {b['booking_date']} {b['start_hour']}:00" for b in mock_bookings]
    except Exception as e:
        print(f"Chat grounding snapshot error: {e}")

    if not sessions_context and mock_game_sessions:
        for s in mock_game_sessions[:8]:
            sessions_context.append(
                f"Match {s['session_id']}: Player {s['player_score']} - OmniBot {s['bot_score']} (Bot DUPR: {s['bot_difficulty']}, Winner: {s['winner']}, Rally Length: {s['rally_length']} shots, Concluding Fault: {s['fault_type']}). Tactical Note: {s['coaching_summary'][:120]}..."
            )

    system_instruction = f"""
You are the AI Pickleball Concierge & Rules Coach for the Spanner Omni Pickleball Game.
You have real-time access to the live database state across all Google Cloud Spanner Omni tables:

[LIVE PLAYER GAME SESSIONS & MATCH HISTORY (GameSessions)]:
{chr(10).join(sessions_context) if sessions_context else "No completed matches recorded yet in Spanner."}

[PLAYER SHOT STATS (Shots)]:
{chr(10).join(shots_context) if shots_context else "No individual shot stats recorded yet."}

[COURT SPECIFICATIONS (Courts)]:
{chr(10).join(courts_context)}

[CURRENT CONFIRMED RESERVATIONS (Bookings)]:
{chr(10).join(bookings_context)}

INSTRUCTIONS:
1. If the user asks "how did I do?", "what is my record?", or asks about their performance, analyze the matches in [LIVE PLAYER GAME SESSIONS & MATCH HISTORY].
   - Cite their exact scores (e.g. You X vs OmniBot Y), whether they won or lost, the bot's DUPR difficulty, rally lengths, and concluding faults.
   - Provide sharp tactical coaching advice on how they can improve based on those match outcomes.
2. If the user asks about court bookings, rates, or availability, use [COURT SPECIFICATIONS] and [CURRENT CONFIRMED RESERVATIONS].
3. If the user asks about pickleball rules (NVZ Kitchen rules, 2-Bounce rule, serving), provide structured, authoritative guidance.
Always use bold text and emojis for readability.
"""

    if ai_client:
        try:
            if hasattr(ai_client, "models"):
                res = ai_client.models.generate_content(
                    model="gemini-3.8-flash",
                    contents=f"{system_instruction}\n\nUser Question: {user_prompt}"
                )
                return jsonify({
                    "response": res.text,
                    "ai_source": "Gemini 3.8 Flash",
                    "spanner_synced": True
                })
            elif hasattr(ai_client, "generate_content"):
                res = ai_client.generate_content(f"{system_instruction}\n\nUser Question: {user_prompt}")
                return jsonify({
                    "response": res.text,
                    "ai_source": "Gemini 1.5 Flash (Live API)",
                    "spanner_synced": True
                })
        except Exception as e:
            print(f"Gemini API invocation note: {e}")

    # Fallback simulated response
    p = user_prompt.lower()
    if "court" in p or "book" in p or "recommend" in p or "indoor" in p:
        resp = (
            "🏓 **Spanner Omni Court Recommendation**\n\n"
            "Based on live availability in our Spanner database:\n"
            "- **For Climate-Controlled Play**: **Center Championship Court** (Pro-Cushion Acrylic, $45/hr) or **Training Pavilion Court 3** ($35/hr).\n"
            "- **For Scenic Casual Games**: **Grandstand Court 2** (Outdoor Hardcourt, $30/hr).\n\n"
            "💡 *Cloud Spanner guarantees zero double-booking conflicts with distributed ACID transactions.*"
        )
    elif "kitchen" in p or "rule" in p or "nvz" in p:
        resp = (
            "📖 **Pickleball Rule Pro-Tip: The Non-Volley Zone (The Kitchen)**\n\n"
            "1. **No Volleys Inside the Kitchen**: You cannot hit the ball out of the air while standing in the 7-foot zone or on the line.\n"
            "2. **Momentum Counts**: If momentum carries your foot into the kitchen after a volley, it is a fault!\n"
            "3. **Bounces Allowed**: If the ball bounces in the kitchen first, you can legally step in and return it."
        )
    else:
        resp = (
            f"🏓 **Spanner Omni AI Concierge**\n\n"
            f"I analyzed your request: *\"{user_prompt}\"*\n\n"
            f"- We have {len(courts_context)} state-of-the-art courts connected to Cloud Spanner.\n"
            f"- Real-time bookings and matchmaking requests are updated with instant transaction consistency."
        )

    return jsonify({
        "response": resp,
        "ai_source": "Spanner Omni AI Engine",
        "spanner_synced": True
    })

@app.route("/api/execute-shot", methods=["POST"])
def api_execute_shot():
    data = request.json or {}
    user_shot = data.get("shot_type", "Crosscourt Soft Dink")
    from_zone = data.get("from_zone", "z_kitchen_left")
    to_zone = data.get("to_zone", "z_kitchen_opp_right")
    speed = float(data.get("speed_mph", 24.5))
    spin = data.get("spin", "Topspin")

    user_shot_id = f"s_usr_{uuid.uuid4().hex[:8]}"
    bot_shot_id = f"s_bot_{uuid.uuid4().hex[:8]}"
    trans_id = f"t_edge_{uuid.uuid4().hex[:8]}"

    # Tactical Bot Counter Logic
    s_lower = user_shot.lower()
    if "speedup" in s_lower:
        bot_counter_shot = "Forehand Body Punch Volley"
        bot_target_zone = "z_mid_left"
        bot_speed = 42.0
        tactical_feedback = (
            f"⚡ **Speedup Countered with Body Punch**: Your speedup reached {speed} mph into the NVZ line. "
            "OmniBot intercepted out of the air before the bounce, attacking your right hip. "
            "💡 *Tip: Disguise speedups off off-balance dinks or aim lower towards the shoe tops to avoid pop-ups.*"
        )
    elif "dink" in s_lower:
        bot_counter_shot = "Crosscourt Roll Dink to Backhand"
        bot_target_zone = "z_kitchen_left"
        bot_speed = 19.5
        tactical_feedback = (
            f"🎯 **Soft Game Sustained**: Excellent control on that {speed} mph soft dink into {to_zone}. "
            "OmniBot responded with a heavy topspin crosscourt roll targeting your backhand corner. "
            "💡 *Tip: Stay patient at the kitchen line and keep your paddle out in front for the 4th-shot reset.*"
        )
    elif "drop" in s_lower:
        bot_counter_shot = "Aggressive Kitchen Volley Roll"
        bot_target_zone = "z_baseline_left"
        bot_speed = 34.0
        tactical_feedback = (
            f"🪂 **3rd-Shot Drop Transition**: Clean arc on your drop ({speed} mph) from deep baseline. "
            "OmniBot stepped in to take it on the rise with a roll to keep you pinned back. "
            "💡 *Tip: Follow through high and move up to the transition zone immediately as the ball crosses the net.*"
        )
    elif "lob" in s_lower:
        bot_counter_shot = "Overhead Smash to Deep Corner"
        bot_target_zone = "z_baseline_right"
        bot_speed = 52.0
        tactical_feedback = (
            f"☄️ **Offensive Lob Defense**: Lob reached {speed} mph but OmniBot backpedaled into position for an overhead smash. "
            "💡 *Tip: Offensive lobs work best when opponents lean forward expecting an aggressive dink.*"
        )
    else:
        bot_counter_shot = "Deep Third-Shot Drop"
        bot_target_zone = "z_kitchen_right"
        bot_speed = 25.0
        tactical_feedback = f"🎾 **Solid Rally Play**: OmniBot responded with a controlled reset to {bot_target_zone}."

    agent_text = tactical_feedback
    if ai_client:
        try:
            prompt = f"""
            You are the OmniBot AI Pickleball Coach running on Spanner Omni.
            The player just executed:
            - Shot: {user_shot} ({speed} mph, {spin})
            - Placement: From {from_zone} to {to_zone}
            - OmniBot Counter: {bot_counter_shot} to {bot_target_zone} ({bot_speed} mph)

            Provide 2-3 sentences of sharp tactical coaching advice on this specific exchange. Use emojis and bold highlights.
            """
            if hasattr(ai_client, "models"):
                res = ai_client.models.generate_content(model="gemini-3.8-flash", contents=prompt)
                if res.text:
                    agent_text = res.text
            elif hasattr(ai_client, "generate_content"):
                res = ai_client.generate_content(prompt)
                if res.text:
                    agent_text = res.text
        except Exception as e:
            print(f"Gemini live coaching note: {e}")

    # Record Shot Telemetry to Spanner Omni
    db = get_spanner_db()
    if db:
        try:
            from google.cloud import spanner
            def record_telemetry(transaction):
                transaction.execute_update(
                    "INSERT OR IGNORE INTO Shots (shot_id, player_id, shot_type, speed_mph, spin_type, outcome, from_zone_id, to_zone_id, created_at) "
                    "VALUES (@id, '101', @stype, @speed, @spin, 'In Play', @fz, @tz, spanner.COMMIT_TIMESTAMP)",
                    params={"id": user_shot_id, "stype": user_shot, "speed": speed, "spin": spin, "fz": from_zone, "tz": to_zone},
                    param_types={"id": spanner.param_types.STRING, "stype": spanner.param_types.STRING, "speed": spanner.param_types.FLOAT64, "spin": spanner.param_types.STRING, "fz": spanner.param_types.STRING, "tz": spanner.param_types.STRING}
                )
            db.run_in_transaction(record_telemetry)
        except Exception:
            pass

    return jsonify({
        "status": "success",
        "user_shot": user_shot,
        "bot_counter_shot": bot_counter_shot,
        "bot_target_zone": bot_target_zone,
        "bot_speed_mph": bot_speed,
        "coaching_analysis": agent_text,
        "spanner_node_created": {
            "user_shot_id": user_shot_id,
            "bot_shot_id": bot_shot_id,
            "edge_transition_id": trans_id
        }
    })

@app.route("/api/rally-end", methods=["POST"])
def api_rally_end():
    data = request.json or {}
    winner = data.get("winner", "player")
    fault_type = data.get("fault_type", "Out of Bounds")
    rally_length = int(data.get("rally_length", 1))
    shot_sequence = data.get("shot_sequence", [])
    user_score = data.get("user_score", 0)
    bot_score = data.get("bot_score", 0)
    bot_difficulty = data.get("bot_difficulty", "5.0")

    bot_label = f"OmniBot ({bot_difficulty} DUPR)"

    analysis = (
        f"🏆 Point won by **{'You' if winner == 'player' else bot_label}** after a **{rally_length}-shot rally**!\n"
        f"Fault: *{fault_type}*.\n"
        f"Score is now **You: {user_score} | {bot_label}: {bot_score}**."
    )

    if ai_client:
        prompt = f"""
        You are the official Gemini AI Pickleball Coach & Tournament Referee running on Spanner Omni.
        A continuous pickleball rally just concluded:
        - Point Winner: {'Human Player' if winner == 'player' else bot_label}
        - Bot Difficulty Level: {bot_difficulty} DUPR
        - Concluding Fault / Winner Event: {fault_type}
        - Total Rally Length: {rally_length} shots
        - Shot Sequence: {json.dumps(shot_sequence)}
        - Updated Match Score: Player {user_score} - Bot {bot_score}

        Give 2-3 sentences of sharp tactical coaching on why this point was won or lost against a {bot_difficulty} DUPR opponent, referencing specific pickleball strategy (e.g. 2-bounce double bounce rule, NVZ positioning, reset technique, paddle control, speedup timing, passing angles). Use bold text and emojis.
        """
        try:
            if hasattr(ai_client, "models"):
                res = ai_client.models.generate_content(model="gemini-3.8-flash", contents=prompt)
                if res.text:
                    analysis = res.text
            elif hasattr(ai_client, "generate_content"):
                res = ai_client.generate_content(prompt)
                if res.text:
                    analysis = res.text
        except Exception as e:
            print(f"Gemini rally analysis note: {e}")

    session_id = f"game_{int(time.time() * 1000)}"
    mock_game_sessions.insert(0, {
        "session_id": session_id,
        "player_score": user_score,
        "bot_score": bot_score,
        "bot_difficulty": str(bot_difficulty),
        "winner": winner,
        "rally_length": rally_length,
        "fault_type": fault_type,
        "coaching_summary": analysis[:2000],
        "created_at": datetime.datetime.now().isoformat()
    })

    spanner_committed = False
    db = get_spanner_db()
    if db:
        try:
            from google.cloud import spanner

            def commit_game_session(transaction):
                # 1. Insert into GameSessions using spanner.COMMIT_TIMESTAMP
                insert_session_sql = """
                    INSERT INTO GameSessions (
                        session_id, player_id, player_score, bot_score, bot_difficulty,
                        winner, rally_length, fault_type, coaching_summary, created_at
                    ) VALUES (
                        @session_id, 101, @player_score, @bot_score, @bot_difficulty,
                        @winner, @rally_length, @fault_type, @coaching_summary, spanner.COMMIT_TIMESTAMP
                    )
                """
                transaction.execute_update(
                    insert_session_sql,
                    params={
                        "session_id": session_id,
                        "player_score": user_score,
                        "bot_score": bot_score,
                        "bot_difficulty": str(bot_difficulty),
                        "winner": winner,
                        "rally_length": rally_length,
                        "fault_type": fault_type,
                        "coaching_summary": analysis[:2000]
                    },
                    param_types={
                        "session_id": spanner.param_types.STRING,
                        "player_score": spanner.param_types.INT64,
                        "bot_score": spanner.param_types.INT64,
                        "bot_difficulty": spanner.param_types.STRING,
                        "winner": spanner.param_types.STRING,
                        "rally_length": spanner.param_types.INT64,
                        "fault_type": spanner.param_types.STRING,
                        "coaching_summary": spanner.param_types.STRING
                    }
                )

                # 2. Persist shot sequence
                prev_shot_id = None
                for idx, shot in enumerate(shot_sequence):
                    shot_id = f"{session_id}_s{idx+1}"
                    stype = shot.get("shot_type", "Dink")
                    speed = float(shot.get("speed_mph", 30.0))
                    spin = shot.get("spin_type", "Flat")
                    outcome = "Winner" if idx == len(shot_sequence) - 1 and winner == shot.get("hitter", "player") else "In Play"
                    if idx == len(shot_sequence) - 1 and winner != shot.get("hitter", "player"):
                        outcome = f"Fault ({fault_type})"

                    from_z = "Player_NVZ" if shot.get("hitter") == "player" else "Bot_NVZ"
                    to_z = "Bot_NVZ" if shot.get("hitter") == "player" else "Player_NVZ"

                    insert_shot_sql = """
                        INSERT OR IGNORE INTO Shots (
                            shot_id, player_id, shot_type, speed_mph, spin_type, outcome, from_zone_id, to_zone_id, created_at
                        ) VALUES (
                            @shot_id, @player_id, @shot_type, @speed, @spin, @outcome, @from_zone, @to_zone, spanner.COMMIT_TIMESTAMP
                        )
                    """
                    transaction.execute_update(
                        insert_shot_sql,
                        params={
                            "shot_id": shot_id,
                            "player_id": "101" if shot.get("hitter") == "player" else "999",
                            "shot_type": stype,
                            "speed": speed,
                            "spin": spin,
                            "outcome": outcome,
                            "from_zone": from_z,
                            "to_zone": to_z
                        },
                        param_types={
                            "shot_id": spanner.param_types.STRING,
                            "player_id": spanner.param_types.STRING,
                            "shot_type": spanner.param_types.STRING,
                            "speed": spanner.param_types.FLOAT64,
                            "spin": spanner.param_types.STRING,
                            "outcome": spanner.param_types.STRING,
                            "from_zone": spanner.param_types.STRING,
                            "to_zone": spanner.param_types.STRING
                        }
                    )

                    if prev_shot_id:
                        trans_id = f"tr_{session_id}_{idx}"
                        insert_trans_sql = """
                            INSERT OR IGNORE INTO ShotTransitions (
                                transition_id, from_shot_id, to_shot_id, rally_depth, pressure_level
                            ) VALUES (
                                @trans_id, @from_shot, @to_shot, @depth, @pressure
                            )
                        """
                        transaction.execute_update(
                            insert_trans_sql,
                            params={
                                "trans_id": trans_id,
                                "from_shot": prev_shot_id,
                                "to_shot": shot_id,
                                "depth": idx + 1,
                                "pressure": "High" if speed > 40 else "Normal"
                            },
                            param_types={
                                "trans_id": spanner.param_types.STRING,
                                "from_shot": spanner.param_types.STRING,
                                "to_shot": spanner.param_types.STRING,
                                "depth": spanner.param_types.INT64,
                                "pressure": spanner.param_types.STRING
                            }
                        )
                    prev_shot_id = shot_id

            db.run_in_transaction(commit_game_session)
            spanner_committed = True
        except Exception as e:
            print(f"Spanner game session commit note: {e}")

    return jsonify({
        "status": "success",
        "analysis": analysis,
        "spanner_committed": spanner_committed
    })

@app.route("/api/performance", methods=["GET"])
def api_get_performance():
    db = get_spanner_db()
    sessions = []
    wins = 0
    losses = 0
    total_rallies = 0

    if db:
        try:
            query = """
                SELECT session_id, player_score, bot_score, bot_difficulty, winner,
                       rally_length, fault_type, coaching_summary, created_at
                FROM GameSessions
                ORDER BY created_at DESC
                LIMIT 20
            """
            with db.snapshot() as snapshot:
                results = snapshot.execute_sql(query)
                for row in results:
                    w = row[4]
                    if w == "player":
                        wins += 1
                    else:
                        losses += 1
                    total_rallies += row[5]
                    sessions.append({
                        "session_id": row[0],
                        "player_score": row[1],
                        "bot_score": row[2],
                        "bot_difficulty": row[3],
                        "winner": row[4],
                        "rally_length": row[5],
                        "fault_type": row[6],
                        "coaching_summary": row[7],
                        "created_at": str(row[8]) if row[8] else None
                    })
        except Exception as e:
            print(f"Spanner performance query note: {e}")

    if not sessions and mock_game_sessions:
        sessions = mock_game_sessions[:20]
        wins = sum(1 for s in sessions if s.get("winner") == "player")
        losses = sum(1 for s in sessions if s.get("winner") != "player")
        total_rallies = sum(s.get("rally_length", 1) for s in sessions)

    total_games = wins + losses
    win_rate = round((wins / total_games * 100), 1) if total_games > 0 else 0

    return jsonify({
        "sessions": sessions,
        "stats": {
            "total_games": total_games,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "avg_rally_length": round(total_rallies / total_games, 1) if total_games > 0 else 0
        }
    })


if __name__ == "__main__":
    print("=" * 60)
    print("  🏓 Starting Spanner Omni Pickleball Game")
    print(f"  Spanner Endpoint:  {SPANNER_EMULATOR_HOST}")
    print(f"  Spanner Project:   {SPANNER_PROJECT}")
    print(f"  Spanner Instance:  {SPANNER_INSTANCE}")
    print(f"  Spanner Database:  {SPANNER_DATABASE}")
    print(f"  Gemini API Key:    {'Configured' if GEMINI_API_KEY else 'Not set'}")
    print("=" * 60)

    # 1. Run schema check & seed asynchronously in background so Flask starts instantly
    threading.Thread(target=init_database_schema_and_seed, daemon=True).start()

    # 2. Start Flask Web Server
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
