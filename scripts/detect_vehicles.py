"""車両検出パイプライン — DOTA学習済みOBBモデル対応

YOLOv8-OBB (DOTA学習) で衛星画像から車両を検出し、
緯度経度に変換してPostGISに保存する。

DOTA車両クラス:
  9: large-vehicle (トラック・バスなど)
  10: small-vehicle (乗用車・バンなど)

使い方:
  python -m scripts.detect_vehicles data/narita_P1_parking_z18.png
  python -m scripts.detect_vehicles data/narita_P1_parking_z18.png --no-db
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image
from shapely.geometry import Point, box as shapely_box
from geoalchemy2.shape import from_shape

from scripts.config import CONFIDENCE_THRESHOLD, MODELS_DIR, NARITA_BBOX
from scripts.db import AnalysisSession, DetectedVehicle, get_session
from scripts.geo_utils import PixelGeoTransformer

# DOTA 車両クラス
DOTA_VEHICLE_CLASSES = {9: "large-vehicle", 10: "small-vehicle"}

# デフォルトモデル
DEFAULT_MODEL = "yolov8l-obb.pt"


def load_model(model_name: str = DEFAULT_MODEL):
    from ultralytics import YOLO
    model_path = MODELS_DIR / model_name
    if model_path.exists():
        return YOLO(str(model_path))
    return YOLO(model_name)


def detect_in_image(
    image_path: str | Path,
    bbox: dict | None = None,
    confidence: float = CONFIDENCE_THRESHOLD,
    model_name: str = DEFAULT_MODEL,
    save_to_db: bool = True,
    image_source: str = "satellite_tiles",
    slice_size: int = 640,
    overlap_ratio: float = 0.2,
) -> dict:
    """OBBモデルで車両検出。大画像はスライス推論で処理。"""
    image_path = Path(image_path)
    if not image_path.exists():
        print(f"画像なし: {image_path}")
        sys.exit(1)

    # メタデータからBBox読み込み
    if bbox is None:
        meta_path = image_path.with_suffix(".json")
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            bbox = meta.get("bbox", NARITA_BBOX)
            image_source = meta.get("server_name", image_source)
        else:
            bbox = NARITA_BBOX

    img = Image.open(image_path)
    w, h = img.size
    print(f"画像: {w}x{h}")

    transformer = PixelGeoTransformer(bbox, w, h)
    resolution = transformer.resolution_meters()
    print(f"解像度: {resolution:.3f} m/px")

    # モデルロード
    print(f"モデル: {model_name}")
    model = load_model(model_name)

    # スライス推論: 大画像を小パッチに分割して検出
    print(f"スライス推論 (slice={slice_size}px, overlap={overlap_ratio:.0%})...")
    stride = int(slice_size * (1 - overlap_ratio))
    all_detections = []
    patch_count = 0

    img_array = np.array(img)

    for y_start in range(0, h, stride):
        for x_start in range(0, w, stride):
            x_end = min(x_start + slice_size, w)
            y_end = min(y_start + slice_size, h)

            # パッチが小さすぎたらスキップ
            if (x_end - x_start) < 100 or (y_end - y_start) < 100:
                continue

            patch = img_array[y_start:y_end, x_start:x_end]
            patch_count += 1

            results = model(
                patch,
                conf=confidence,
                imgsz=slice_size,
                verbose=False,
            )

            for result in results:
                if result.obb is None or len(result.obb) == 0:
                    continue

                for i in range(len(result.obb)):
                    cls_id = int(result.obb.cls[i].item())
                    if cls_id not in DOTA_VEHICLE_CLASSES:
                        continue

                    conf_val = result.obb.conf[i].item()

                    # OBBの中心座標 (パッチ内)
                    xywhr = result.obb.xywhr[i].tolist()
                    cx_patch, cy_patch = xywhr[0], xywhr[1]
                    obb_w, obb_h, angle = xywhr[2], xywhr[3], xywhr[4]

                    # パッチ座標 → 全体画像座標
                    cx_global = cx_patch + x_start
                    cy_global = cy_patch + y_start

                    # 全体画像座標 → 緯度経度
                    lon, lat = transformer.pixel_to_lonlat(cx_global, cy_global)

                    all_detections.append({
                        "cx_px": round(cx_global, 1),
                        "cy_px": round(cy_global, 1),
                        "obb_w_px": round(obb_w, 1),
                        "obb_h_px": round(obb_h, 1),
                        "angle_rad": round(angle, 4),
                        "confidence": round(conf_val, 4),
                        "class_label": DOTA_VEHICLE_CLASSES[cls_id],
                        "lon": lon,
                        "lat": lat,
                    })

    print(f"パッチ数: {patch_count}")
    print(f"検出(重複あり): {len(all_detections)} 台")

    # NMS: 近接する検出を統合（重複除去）
    detections = nms_by_distance(all_detections, dist_threshold=5.0)
    print(f"検出(NMS後): {len(detections)} 台")

    # クラス別集計
    class_counts = {}
    for d in detections:
        cls = d["class_label"]
        class_counts[cls] = class_counts.get(cls, 0) + 1
    for cls, cnt in sorted(class_counts.items()):
        print(f"  {cls}: {cnt}")

    # アノテーション画像を生成
    annotated_path = image_path.parent / f"{image_path.stem}_detected{image_path.suffix}"
    draw_detections(img, detections, annotated_path)
    print(f"アノテーション: {annotated_path}")

    # DB保存
    if save_to_db and detections:
        print("DB保存中...")
        db = get_session()
        try:
            bbox_geom = shapely_box(bbox["west"], bbox["south"], bbox["east"], bbox["north"])
            sess = AnalysisSession(
                analyzed_at=datetime.utcnow(),
                image_source=image_source,
                image_path=str(image_path),
                bbox=from_shape(bbox_geom, srid=4326),
                resolution_m=resolution,
                model_name=model_name,
                model_version="yolov8-obb-dota",
                total_detected=len(detections),
            )
            db.add(sess)
            db.flush()

            for d in detections:
                db.add(DetectedVehicle(
                    session_id=sess.id,
                    location=from_shape(Point(d["lon"], d["lat"]), srid=4326),
                    bbox_pixel=[d["cx_px"], d["cy_px"], d["obb_w_px"], d["obb_h_px"]],
                    confidence=d["confidence"],
                    class_label=d["class_label"],
                ))
            db.commit()
            print(f"DB保存完了: session_id={sess.id}, vehicles={len(detections)}")
        except Exception as e:
            db.rollback()
            print(f"DBエラー: {e}")
            raise
        finally:
            db.close()

    # GeoJSON
    geojson_path = image_path.parent / f"{image_path.stem}_vehicles.geojson"
    geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [d["lon"], d["lat"]]},
            "properties": {
                "confidence": d["confidence"],
                "class": d["class_label"],
            },
        } for d in detections],
    }
    geojson_path.write_text(json.dumps(geojson, indent=2))
    print(f"GeoJSON: {geojson_path}")

    return {
        "total": len(detections),
        "by_class": class_counts,
        "image": str(image_path),
        "annotated": str(annotated_path),
        "geojson": str(geojson_path),
        "resolution_m": resolution,
    }


def nms_by_distance(detections: list, dist_threshold: float = 5.0) -> list:
    """距離ベースのNMS: 近すぎる検出の重複を除去（信頼度の高い方を残す）"""
    if not detections:
        return []

    # 信頼度で降順ソート
    sorted_dets = sorted(detections, key=lambda d: d["confidence"], reverse=True)
    keep = []
    used = set()

    for i, det in enumerate(sorted_dets):
        if i in used:
            continue
        keep.append(det)
        for j in range(i + 1, len(sorted_dets)):
            if j in used:
                continue
            dx = det["cx_px"] - sorted_dets[j]["cx_px"]
            dy = det["cy_px"] - sorted_dets[j]["cy_px"]
            dist = (dx ** 2 + dy ** 2) ** 0.5
            if dist < dist_threshold:
                used.add(j)

    return keep


def draw_detections(img: Image.Image, detections: list, output_path: Path):
    """検出結果を画像に描画"""
    from PIL import ImageDraw

    draw_img = img.copy()
    draw = ImageDraw.Draw(draw_img)

    colors = {"small-vehicle": (0, 255, 0), "large-vehicle": (255, 165, 0)}

    for d in detections:
        cx, cy = d["cx_px"], d["cy_px"]
        hw, hh = d["obb_w_px"] / 2, d["obb_h_px"] / 2
        color = colors.get(d["class_label"], (255, 0, 0))

        # 簡易矩形 (回転は省略、視認性優先)
        draw.rectangle([cx - hw, cy - hh, cx + hw, cy + hh], outline=color, width=2)
        # 中心点
        draw.ellipse([cx - 2, cy - 2, cx + 2, cy + 2], fill=color)

    draw_img.save(str(output_path))


def main():
    parser = argparse.ArgumentParser(description="衛星画像から車両検出 (DOTA OBBモデル)")
    parser.add_argument("image", help="入力画像パス")
    parser.add_argument("--bbox", help="BBox JSON")
    parser.add_argument("--confidence", type=float, default=0.15,
                        help="信頼度閾値 (default: 0.15)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--no-db", action="store_true")
    parser.add_argument("--source", default="satellite_tiles")
    parser.add_argument("--slice-size", type=int, default=640,
                        help="スライスサイズ (default: 640)")
    parser.add_argument("--overlap", type=float, default=0.2,
                        help="スライスオーバーラップ率 (default: 0.2)")
    args = parser.parse_args()

    detect_in_image(
        args.image,
        bbox=json.loads(args.bbox) if args.bbox else None,
        confidence=args.confidence,
        model_name=args.model,
        save_to_db=not args.no_db,
        image_source=args.source,
        slice_size=args.slice_size,
        overlap_ratio=args.overlap,
    )


if __name__ == "__main__":
    main()
