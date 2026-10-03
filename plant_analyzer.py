import json
import logging
import os
from pathlib import Path

from google import genai
from google.genai import types
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)


def _get_api_key() -> str:
    """
    หา API key จาก environment variable โดยรองรับทั้ง GEMINI_API_KEY
    และ GOOGLE_API_KEY (ชื่อที่ google-genai รองรับ) แล้วแจ้ง error ชัดเจน
    ถ้าไม่พบเลย แทนที่จะปล่อยให้ genai.Client() error แบบเข้าใจยาก
    """
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key or not api_key.strip():
        raise PlantAnalysisError(
            "ไม่พบ API Key — กรุณาตั้งค่า environment variable GEMINI_API_KEY ก่อนรัน\n"
            "Windows PowerShell (ชั่วคราว): $env:GEMINI_API_KEY=\"คีย์จริงของคุณ\"\n"
            "Windows PowerShell (ถาวร): setx GEMINI_API_KEY \"คีย์จริงของคุณ\" "
            "(ต้องปิด-เปิด PowerShell ใหม่หลังรันคำสั่งนี้)\n"
            "macOS/Linux: export GEMINI_API_KEY=\"คีย์จริงของคุณ\""
        )
    return api_key.strip()


_client: genai.Client | None = None


def _get_client() -> genai.Client:
    """สร้าง client แบบ lazy — เพื่อให้ error ข้อความชัดเจนแสดงตอนเรียกใช้จริง
    ไม่ใช่ตอน import module (ซึ่งจะทำให้ Streamlit ล่มตั้งแต่เปิดหน้าเว็บ)"""
    global _client
    if _client is None:
        _client = genai.Client(api_key=_get_api_key())
    return _client

PROMPT = """
คุณเป็นผู้เชี่ยวชาญด้านพฤกษศาสตร์ การเกษตร และการดูแลไม้ประดับ/ผักสวนครัวแปลงเล็ก...
(ใส่ข้อความ Prompt ทั้งหมดตรงนี้)
"""

# Schema ที่คาดหวัง — ใช้ตรวจสอบว่า Gemini ตอบครบทุกฟิลด์
REQUIRED_FIELDS = {
    "plant_name": str,
    "plant_type": str,
    "health_analysis": str,
    "pest_or_disease": str,
    "water_interval_days": int,
    "watering_instructions": str,
    "sunlight_requirement": str,
    "care_tips": str,
}

MAX_RETRIES = 2  # จำนวนครั้งที่ยอมให้ retry เมื่อ JSON parse ล้มเหลว


class PlantAnalysisError(Exception):
    """เกิดข้อผิดพลาดระหว่างวิเคราะห์ภาพพืช"""


def _validate_schema(data: dict) -> dict:
    """ตรวจสอบว่า dict มีฟิลด์ครบและชนิดข้อมูลถูกต้อง พร้อมพยายามแก้ไขเล็กน้อย"""
    if not isinstance(data, dict):
        raise PlantAnalysisError(f"ผลลัพธ์ไม่ใช่ JSON object: {type(data)}")

    missing = [f for f in REQUIRED_FIELDS if f not in data]
    if missing:
        raise PlantAnalysisError(f"ฟิลด์ที่ขาดหายไปใน JSON: {missing}")

    # water_interval_days ต้องเป็น int — พยายามแปลงถ้า Gemini ส่งมาเป็น string เช่น "3"
    raw_interval = data["water_interval_days"]
    if isinstance(raw_interval, bool):
        raise PlantAnalysisError("water_interval_days ต้องเป็นตัวเลข ไม่ใช่ boolean")
    if isinstance(raw_interval, str):
        try:
            raw_interval = int(raw_interval.strip())
        except ValueError:
            raise PlantAnalysisError(
                f"water_interval_days แปลงเป็นจำนวนเต็มไม่ได้: {raw_interval!r}"
            )
    if not isinstance(raw_interval, int):
        try:
            raw_interval = int(raw_interval)
        except (TypeError, ValueError):
            raise PlantAnalysisError(
                f"water_interval_days ไม่ใช่จำนวนเต็ม: {raw_interval!r}"
            )
    data["water_interval_days"] = max(1, raw_interval)  # กันค่า 0 หรือติดลบ

    for field in REQUIRED_FIELDS:
        if field == "water_interval_days":
            continue
        if not isinstance(data[field], str) or not data[field].strip():
            raise PlantAnalysisError(f"ฟิลด์ {field} ต้องเป็นข้อความและไม่ว่างเปล่า")

    return data


def _load_image(image_path: str) -> Image.Image:
    path = Path(image_path)
    if not path.exists():
        raise PlantAnalysisError(f"ไม่พบไฟล์รูปภาพ: {image_path}")
    if path.stat().st_size == 0:
        raise PlantAnalysisError(f"ไฟล์รูปภาพว่างเปล่า: {image_path}")
    try:
        image = Image.open(path)
        image.load()  # บังคับให้อ่านไฟล์จริง เพื่อจับไฟล์เสียหายตั้งแต่ตรงนี้
        return image
    except UnidentifiedImageError:
        raise PlantAnalysisError(f"ไฟล์นี้ไม่ใช่รูปภาพที่รองรับ: {image_path}")
    except OSError as e:
        raise PlantAnalysisError(f"เปิดไฟล์รูปภาพไม่สำเร็จ: {e}")


def _call_gemini(image: Image.Image) -> str:
    try:
        client = _get_client()
        response = client.models.generate_content(
            model="gemini-3.7-flash",
            contents=[image, PROMPT],
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            ),
        )
    except Exception as e:
        # ครอบคลุม network error, rate limit, invalid API key ฯลฯ จาก google-genai
        raise PlantAnalysisError(f"เรียก Gemini API ไม่สำเร็จ: {e}") from e

    text = getattr(response, "text", None)
    if not text or not text.strip():
        raise PlantAnalysisError("Gemini ตอบกลับมาเป็นข้อความว่างเปล่า")

    return text


def _strip_code_fences(text: str) -> str:
    """เผื่อกรณี Gemini ยังคงห่อ ```json ... ``` มาทั้งที่ตั้ง response_mime_type แล้ว"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    return cleaned.strip()


def analyze_plant_image(image_path: str) -> dict:
    """
    วิเคราะห์รูปภาพพืชด้วย Gemini แล้วคืนค่าเป็น dict ตาม schema ที่กำหนด

    Raises:
        PlantAnalysisError: เมื่อไฟล์ไม่ถูกต้อง, เรียก API ไม่สำเร็จ,
                             หรือผลลัพธ์ไม่ตรง schema แม้ retry แล้ว
    """
    image = _load_image(image_path)

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 2):  # ครั้งแรก + retry สูงสุด MAX_RETRIES ครั้ง
        try:
            raw_text = _call_gemini(image)
            cleaned = _strip_code_fences(raw_text)
            data = json.loads(cleaned)
            return _validate_schema(data)
        except json.JSONDecodeError as e:
            last_error = PlantAnalysisError(
                f"แปลง JSON ไม่สำเร็จ (ครั้งที่ {attempt}): {e}"
            )
            logger.warning(str(last_error))
        except PlantAnalysisError as e:
            last_error = e
            logger.warning("วิเคราะห์ล้มเหลว (ครั้งที่ %d): %s", attempt, e)

    raise last_error or PlantAnalysisError("วิเคราะห์รูปภาพไม่สำเร็จโดยไม่ทราบสาเหตุ")


if __name__ == "__main__":
    # ตัวอย่างการใช้งานพร้อมจับ error ฝั่งเรียกใช้
    try:
        result = analyze_plant_image("sample_plant.jpg")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except PlantAnalysisError as e:
        print(f"เกิดข้อผิดพลาด: {e}")
