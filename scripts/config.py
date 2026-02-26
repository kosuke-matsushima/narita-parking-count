"""共通設定"""
import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# DB接続
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5433")
DB_NAME = os.getenv("DB_NAME", "parking")
DB_USER = os.getenv("DB_USER", "parking")
DB_PASSWORD = os.getenv("DB_PASSWORD", "parking_pass")
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# 成田空港 — Google Maps実測ベースの座標
# P1: 第1ターミナル駐車場
# P2: 第2ターミナル駐車場
NARITA_CENTER = (35.7680, 140.3880)

NARITA_BBOX = {
    "west": 140.3750,
    "south": 35.7550,
    "east": 140.4000,
    "north": 35.7830,
}

# データパス
DATA_DIR = PROJECT_ROOT / "data"
MODELS_DIR = PROJECT_ROOT / "models"
DATA_DIR.mkdir(exist_ok=True)
MODELS_DIR.mkdir(exist_ok=True)

# YOLOv8
YOLO_MODEL = os.getenv("YOLO_MODEL", "yolov8x.pt")
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.25"))
