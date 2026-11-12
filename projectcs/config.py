from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "projectcs.sqlite"
ROSTER = [
    {"nickname": "Grvpe", "role": "Da definire · no lurk iniziale"},
    {"nickname": "-Zoen-", "role": "Candidato AWP"},
    {"nickname": "oSpEcTeRo", "role": "IGL"},
    {"nickname": "LEWALKER", "role": "Candidato AWP"},
    {"nickname": "Senshe", "role": "Entry"},
]
NAMES = [p["nickname"] for p in ROSTER]
PRIORITY_MAPS = ["ancient", "anubis", "dust2"]
