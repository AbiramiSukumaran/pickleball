# Spanner Omni Pickleball Game 🏓

An interactive, visual **Pickleball Tactical Game Arena** powered by **Google Cloud Spanner Omni** (distributed ACID consistency and graph telemetry) and **Google Gemini** (tactical coaching & counter-shot calculation).

---

## 🎮 Features

- **Animated Visual Court Arena**: 2.5D visual pickleball court with animated playable characters (You vs OmniBot-3000) and realistic 60 FPS wiffle ball trajectory physics.
- **Spanner Omni Graph Engine**: Each rally shot and counter-response logs shot nodes (`s1:Shot`, `s2:Shot`) and transition relationships (`[:TRANSITIONED_TO]`) directly in Spanner.
- **Agentic AI Tactical Coach**: Powered by Gemini with live Spanner telemetry grounding.
- **Web Audio API Effects**: Pure synthesized paddle hit pops, court bounce thuds, and speedup swooshes.
- **Interactive Control Deck**: Choose from Kitchen Soft Dinks, 3rd-Shot Drops, Shoulder Speedups, and Offensive Lobs with spin control.

---

## 🚀 Quick Start

### 1. In Google Cloud Shell (Recommended)
```bash
pip install -r requirements.txt
```

### 2. Setting up Spanner Omni
```bash
# Create persistent storage volume
docker volume create spanner_omni_data

docker pull us-docker.pkg.dev/spanner-omni/images/spanner-omni:2026.r2.1-beta

# Run Spanner Omni in single-server mode
docker run -d --name spanner-omni \
  -p 9010:9010 -p 9020:9020 \
  -v spanner_omni_data:/spanner \
  us-docker.pkg.dev/spanner-omni/images/spanner-omni:2026.r2.1-beta \
  start-single-server

#check if it's up
docker ps

# Assuming you have cloned this repo and update the .env
# Run the app:
python app.py
```

Access the visual arena.

---

## 📂 Project Structure

```
spanner-omni-project/
├── app.py                  # Flask backend, Spanner transactions & Gemini AI
├── requirements.txt        # Python dependencies
├── Dockerfile              # Container definition
├── start.sh                # Automated bootstrap script
├── .env                    # Environment variables (Spanner config & Gemini key)
├── README.md               # Documentation
└── templates/
    └── index.html          # 2.5D visual animated court arena
```
