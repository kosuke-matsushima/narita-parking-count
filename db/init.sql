-- PostGIS拡張を有効化
CREATE EXTENSION IF NOT EXISTS postgis;

-- ============================================================
-- 解析セッション: 1回の画像取得〜検出を1セッションとする
-- ============================================================
CREATE TABLE IF NOT EXISTS analysis_sessions (
    id              SERIAL PRIMARY KEY,
    captured_at     TIMESTAMPTZ,
    analyzed_at     TIMESTAMPTZ DEFAULT NOW(),
    image_source    TEXT,
    image_path      TEXT,
    bbox            GEOMETRY(POLYGON, 4326),
    resolution_m    REAL,
    model_name      TEXT,
    model_version   TEXT,
    total_detected  INTEGER DEFAULT 0,
    notes           TEXT
);

-- ============================================================
-- 検出車両: 1台1レコード、Pointジオメトリで位置を保持
-- ============================================================
CREATE TABLE IF NOT EXISTS detected_vehicles (
    id              SERIAL PRIMARY KEY,
    session_id      INTEGER NOT NULL REFERENCES analysis_sessions(id) ON DELETE CASCADE,
    location        GEOMETRY(POINT, 4326) NOT NULL,
    bbox_pixel      JSONB,
    confidence      REAL NOT NULL,
    class_label     TEXT DEFAULT 'car',
    is_correct      BOOLEAN,
    verified_at     TIMESTAMPTZ,
    verified_by     TEXT,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_vehicles_location ON detected_vehicles USING GIST (location);
CREATE INDEX IF NOT EXISTS idx_vehicles_session ON detected_vehicles (session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_bbox ON analysis_sessions USING GIST (bbox);

-- ============================================================
-- 駐車場エリア定義
-- ============================================================
CREATE TABLE IF NOT EXISTS parking_areas (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    area            GEOMETRY(POLYGON, 4326) NOT NULL,
    capacity        INTEGER,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_parking_areas ON parking_areas USING GIST (area);

-- 成田空港駐車場の初期データ (Google Maps実測値ベース)
INSERT INTO parking_areas (name, area, capacity, notes) VALUES
(
    'P1 (第1ターミナル)',
    ST_GeomFromText('POLYGON((140.3830 35.7780, 140.3930 35.7780, 140.3930 35.7710, 140.3830 35.7710, 140.3830 35.7780))', 4326),
    1800,
    '第1ターミナル直結 P1駐車場'
),
(
    'P2 (第2ターミナル)',
    ST_GeomFromText('POLYGON((140.3810 35.7660, 140.3910 35.7660, 140.3910 35.7590, 140.3810 35.7590, 140.3810 35.7660))', 4326),
    1700,
    '第2ターミナル直結 P2駐車場'
);
