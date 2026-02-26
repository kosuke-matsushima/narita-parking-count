"""座標変換ユーティリティ — ピクセル座標 ↔ 緯度経度"""
import numpy as np


class PixelGeoTransformer:
    """ピクセル座標と地理座標の変換器"""

    def __init__(self, bbox: dict, image_width: int, image_height: int):
        self.bbox = bbox
        self.width = image_width
        self.height = image_height
        self.lon_per_px = (bbox["east"] - bbox["west"]) / image_width
        self.lat_per_px = (bbox["north"] - bbox["south"]) / image_height

    def pixel_to_lonlat(self, px_x: float, px_y: float) -> tuple[float, float]:
        """ピクセル座標 → (longitude, latitude)"""
        lon = self.bbox["west"] + px_x * self.lon_per_px
        lat = self.bbox["north"] - px_y * self.lat_per_px
        return (lon, lat)

    def lonlat_to_pixel(self, lon: float, lat: float) -> tuple[float, float]:
        """(longitude, latitude) → ピクセル座標"""
        px_x = (lon - self.bbox["west"]) / self.lon_per_px
        px_y = (self.bbox["north"] - lat) / self.lat_per_px
        return (px_x, px_y)

    def bbox_pixel_to_center_lonlat(self, x1, y1, x2, y2) -> tuple[float, float]:
        """BBoxの中心 → (lon, lat)"""
        return self.pixel_to_lonlat((x1 + x2) / 2, (y1 + y2) / 2)

    def resolution_meters(self) -> float:
        """おおよその空間解像度 (m/px)"""
        lat_center = (self.bbox["north"] + self.bbox["south"]) / 2
        m_per_deg_lon = 111_320 * np.cos(np.radians(lat_center))
        m_per_deg_lat = 110_540
        return (self.lon_per_px * m_per_deg_lon + self.lat_per_px * m_per_deg_lat) / 2
