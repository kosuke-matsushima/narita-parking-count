"""実衛星画像取得スクリプト

国土地理院シームレス空中写真タイル (zoom 18, ~0.5m/px) を
ダウンロード・ステッチして成田空港駐車場の高解像度画像を生成する。

データソース:
  - GSI Japan: https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg
  - 無料・政府公開データ・要attribution

使い方:
  python -m scripts.fetch_image --area P1
  python -m scripts.fetch_image --area P2
  python -m scripts.fetch_image --all
"""
import argparse
import json
import math
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from io import BytesIO
from pathlib import Path

import mercantile
from PIL import Image

from scripts.config import DATA_DIR, NARITA_BBOX

TILE_SERVERS = {
    "gsi": {
        "url": "https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg",
        "max_zoom": 18,
        "attribution": "国土地理院",
        "name": "GSI Seamless Photo",
    },
    "gsi_ort": {
        "url": "https://cyberjapandata.gsi.go.jp/xyz/ort/{z}/{x}/{y}.jpg",
        "max_zoom": 18,
        "attribution": "国土地理院 正射画像",
        "name": "GSI Ortho",
    },
    "esri": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "max_zoom": 19,
        "attribution": "Esri",
        "name": "Esri World Imagery",
    },
}

# 成田空港駐車場エリア定義 (正確な座標)
# 成田空港: 35.765, 140.386 付近
# P1: 第1ターミナル北側の大型立体駐車場
# P2: 第2ターミナル南側の駐車場
NARITA_PARKING_AREAS = {
    "P1": {
        "name": "P1 第1ターミナル駐車場",
        "bbox": {"west": 140.3830, "south": 35.7710, "east": 140.3930, "north": 35.7780},
    },
    "P2": {
        "name": "P2 第2ターミナル駐車場",
        "bbox": {"west": 140.3810, "south": 35.7590, "east": 140.3910, "north": 35.7660},
    },
    "全体": {
        "name": "成田空港 全体",
        "bbox": {"west": 140.3750, "south": 35.7550, "east": 140.4000, "north": 35.7830},
    },
}


def fetch_tile(url: str, retries: int = 3) -> bytes | None:
    """1枚のタイルをダウンロード"""
    headers = {
        "User-Agent": "NaritaParkingResearch/1.0",
        "Referer": "https://maps.gsi.go.jp/",
    }
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read()
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1 * (attempt + 1))
            else:
                print(f"  FAIL: {url} — {e}")
                return None


def download_and_stitch(
    bbox: dict,
    zoom: int,
    server: str = "gsi",
    output_path: Path | None = None,
    max_workers: int = 4,
) -> dict:
    """指定範囲のタイルをダウンロード → ステッチ → 保存"""
    srv = TILE_SERVERS[server]
    url_tpl = srv["url"]
    zoom = min(zoom, srv["max_zoom"])

    tiles = list(mercantile.tiles(bbox["west"], bbox["south"], bbox["east"], bbox["north"], zooms=zoom))
    if not tiles:
        print("ERROR: タイルなし")
        sys.exit(1)

    x_min = min(t.x for t in tiles)
    x_max = max(t.x for t in tiles)
    y_min = min(t.y for t in tiles)
    y_max = max(t.y for t in tiles)
    cols = x_max - x_min + 1
    rows = y_max - y_min + 1

    lat_c = (bbox["north"] + bbox["south"]) / 2
    mpp = 156543.04 * math.cos(math.radians(lat_c)) / (2 ** zoom)

    print(f"サーバー: {srv['name']}")
    print(f"ズーム: {zoom} | 解像度: {mpp:.3f} m/px")
    print(f"タイル: {cols}x{rows} = {len(tiles)}枚")

    tile_size = 256
    canvas = Image.new("RGB", (cols * tile_size, rows * tile_size), (200, 200, 200))

    ok = 0
    ng = 0

    def dl(tile):
        url = url_tpl.format(z=tile.z, x=tile.x, y=tile.y)
        return tile, fetch_tile(url)

    print("ダウンロード中...")
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(dl, t): t for t in tiles}
        for f in as_completed(futures):
            tile, data = f.result()
            if data:
                try:
                    img = Image.open(BytesIO(data))
                    canvas.paste(img, ((tile.x - x_min) * tile_size, (tile.y - y_min) * tile_size))
                    ok += 1
                except Exception:
                    ng += 1
            else:
                ng += 1
            done = ok + ng
            if done % 20 == 0 or done == len(tiles):
                print(f"  {done}/{len(tiles)} (ok={ok} ng={ng})")

    print(f"完了: {ok}/{len(tiles)}")
    if ok == 0:
        print("ERROR: タイル取得ゼロ")
        sys.exit(1)

    # 正確なBBox計算
    nw = mercantile.bounds(x_min, y_min, zoom)
    se = mercantile.bounds(x_max, y_max, zoom)
    stitched_bbox = {"west": nw.west, "north": nw.north, "east": se.east, "south": se.south}

    if output_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = DATA_DIR / f"narita_{server}_z{zoom}_{ts}.png"

    canvas.save(str(output_path), quality=95)
    print(f"保存: {output_path} ({canvas.size[0]}x{canvas.size[1]})")

    meta = {
        "bbox": stitched_bbox,
        "requested_bbox": bbox,
        "width": canvas.size[0],
        "height": canvas.size[1],
        "zoom": zoom,
        "resolution_m_per_px": round(mpp, 4),
        "server": server,
        "server_name": srv["name"],
        "attribution": srv["attribution"],
        "tiles_ok": ok,
        "tiles_ng": ng,
        "tiles_total": len(tiles),
        "created_at": datetime.now().isoformat(),
        "source": "satellite_tiles",
    }
    meta_path = output_path.with_suffix(".json")
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    print(f"メタデータ: {meta_path}")

    return meta


def fetch_narita_parking(area_key: str = "P1", zoom: int = 18, server: str = "gsi") -> dict:
    """成田空港の指定駐車場エリア画像を取得"""
    if area_key not in NARITA_PARKING_AREAS:
        print(f"エリア選択肢: {list(NARITA_PARKING_AREAS.keys())}")
        sys.exit(1)
    area = NARITA_PARKING_AREAS[area_key]
    print(f"\n=== {area['name']} ===")
    out = DATA_DIR / f"narita_{area_key}_{server}_z{zoom}.png"
    return download_and_stitch(area["bbox"], zoom, server, out)


def main():
    parser = argparse.ArgumentParser(description="成田空港駐車場の衛星画像を取得")
    parser.add_argument("--area", choices=list(NARITA_PARKING_AREAS.keys()), default="P1")
    parser.add_argument("--server", choices=list(TILE_SERVERS.keys()), default="gsi")
    parser.add_argument("--zoom", type=int, default=18)
    parser.add_argument("--all", action="store_true", help="全エリアを取得")
    parser.add_argument("--bbox", help="カスタムBBox JSON")
    parser.add_argument("--output", help="出力パス")
    args = parser.parse_args()

    if args.bbox:
        download_and_stitch(json.loads(args.bbox), args.zoom, args.server,
                            Path(args.output) if args.output else None)
    elif args.all:
        for key in NARITA_PARKING_AREAS:
            fetch_narita_parking(key, args.zoom, args.server)
    else:
        fetch_narita_parking(args.area, args.zoom, args.server)


if __name__ == "__main__":
    main()
