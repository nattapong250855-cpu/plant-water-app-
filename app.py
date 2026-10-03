import os
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
import streamlit as st
from PIL import Image
from google import genai
from google.genai import types

# -----------------------------------------------------------------------------
# 1. การตั้งค่าหน้าจอ Streamlit (Config Page)
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Smart Plant Water Reminder",
    page_icon="🌱",
    layout="centered"
)

# ปรับแต่ง UI โทนพาสเทล เขียว-เหลือง-ชมพู ฟอนต์โค้งมนน่ารัก
st.markdown("""
<link href="https://fonts.googleapis.com/css2?family=Mali:wght@400;600;700&display=swap" rel="stylesheet">
<style>
    html, body, .stApp, p, span, label, div {
        font-family: 'Mali', sans-serif !important;
    }
    .stApp {
        background: linear-gradient(180deg, #F3FBF2 0%, #FFFDF0 50%, #FFF4F8 100%);
    }
    h1, h2, h3 {
        color: #2D7A4F !important;
        font-weight: 700 !important;
    }
    p, span, label, li {
        color: #2D2D2D !important;
    }

    /* การ์ด Metric (ชื่อพืช/ประเภท/รอบรดน้ำ) */
    div[data-testid="stMetric"] {
        background-color: #FFFFFF;
        border: 3px solid #BDECC9;
        border-radius: 20px;
        padding: 14px 10px;
        text-align: center;
        box-shadow: 0 4px 10px rgba(0,0,0,0.08);
    }
    div[data-testid="stMetricLabel"] {
        color: #E8A7C4 !important;
        font-weight: 600 !important;
    }
    div[data-testid="stMetricValue"] {
        color: #2D2D2D !important;
        white-space: normal !important;
        overflow: visible !important;
        text-overflow: unset !important;
        font-size: 1.15rem !important;
        line-height: 1.3 !important;
        word-break: break-word;
    }

    /* ปุ่มทุกปุ่ม โทนเขียวพาสเทล ตัวหนังสือขาว ทรงมนน่ารัก */
    .stButton > button {
        background-color: #8FE3AE;
        color: #FFFFFF !important;
        border-radius: 24px;
        border: none;
        font-weight: 700;
        padding: 8px 20px;
        box-shadow: 0 3px 6px rgba(0,0,0,0.12);
    }
    .stButton > button:hover {
        background-color: #6FD191;
        color: #FFFFFF !important;
    }

    /* กล่อง expander (รายการต้นไม้ที่ติดตาม) โทนชมพูพาสเทล */
    div[data-testid="stExpander"] {
        border-radius: 18px;
        background-color: #FFE9F3;
        border: 2px solid #FFC7E0;
    }
    div[data-testid="stExpander"] p, div[data-testid="stExpander"] label {
        color: #2D2D2D !important;
    }
    div[data-testid="stExpander"] summary {
        color: #C4477A !important;
        font-weight: 700 !important;
    }

    /* Sidebar โทนเหลืองพาสเทล */
    section[data-testid="stSidebar"] {
        background-color: #FFF8DC;
    }
    section[data-testid="stSidebar"] h1, section[data-testid="stSidebar"] h2,
    section[data-testid="stSidebar"] h3, section[data-testid="stSidebar"] p,
    section[data-testid="stSidebar"] label {
        color: #6B5B1E !important;
    }

    /* กล่องแจ้งเตือนทุกชนิด (success/warning/error) ขอบมนพาสเทล */
    div[data-testid="stAlert"] {
        border-radius: 16px;
        box-shadow: 0 3px 6px rgba(0,0,0,0.08);
    }
    div[data-testid="stAlert"] p {
        color: #2D2D2D !important;
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)

REMINDERS_FILE = Path("watering_reminders.json")

# -----------------------------------------------------------------------------
# 2. ฟังก์ชันจัดการรายการแจ้งเตือนรดน้ำ (บันทึกลงไฟล์ JSON บนเซิร์ฟเวอร์)
# -----------------------------------------------------------------------------
def load_reminders() -> list[dict]:
    if REMINDERS_FILE.exists():
        try:
            return json.loads(REMINDERS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
    return []


def save_reminders(reminders: list[dict]) -> None:
    REMINDERS_FILE.write_text(
        json.dumps(reminders, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def add_reminder(plant_name: str, interval_days: int, plant_type: str) -> None:
    reminders = load_reminders()
    today = datetime.now().date()
    next_water = today + timedelta(days=interval_days)
    reminders.append({
        "id": f"{plant_name}-{int(time.time())}",
        "plant_name": plant_name,
        "plant_type": plant_type,
        "interval_days": interval_days,
        "last_watered": today.isoformat(),
        "next_watering": next_water.isoformat(),
    })
    save_reminders(reminders)


def mark_watered(reminder_id: str) -> None:
    reminders = load_reminders()
    today = datetime.now().date()
    for r in reminders:
        if r["id"] == reminder_id:
            r["last_watered"] = today.isoformat()
            r["next_watering"] = (today + timedelta(days=r["interval_days"])).isoformat()
    save_reminders(reminders)


def delete_reminder(reminder_id: str) -> None:
    reminders = load_reminders()
    reminders = [r for r in reminders if r["id"] != reminder_id]
    save_reminders(reminders)


# -----------------------------------------------------------------------------
# 3. ฟังก์ชันประมวลผลรูปภาพด้วย Gemini API (พร้อม resize รูป + fallback หลายโมเดล)
# -----------------------------------------------------------------------------
def analyze_plant(image: Image.Image, api_key: str) -> dict:
    """ส่งรูปภาพพร้อม Prompt ให้ Gemini วิเคราะห์ และคืนค่าเป็น Python Dictionary"""
    client = genai.Client(api_key=api_key)

    # ย่อขนาดรูปก่อนส่ง ป้องกันไฟล์ใหญ่เกินไปทำให้ timeout/error
    max_size = (1024, 1024)
    if image.mode != "RGB":
        image = image.convert("RGB")
    image.thumbnail(max_size, Image.LANCZOS)

    prompt = """
    คุณเป็นผู้เชี่ยวชาญด้านพฤกษศาสตร์ การเกษตร และการดูแลไม้ประดับ/ผักสวนครัวแปลงเล็ก

    โปรดวิเคราะห์รูปภาพพืชที่แนบมานี้อย่างละเอียด โดยเน้นสังเกตชนิดพืช สภาพใบ ลำต้น และความชื้นของหน้าดิน จากนั้นให้ตอบกลับในรูปแบบ JSON ภาษาไทย โดยมีโครงสร้าง Schema ตามนี้เท่านั้น:

    {
      "plant_name": "ชื่อพืช (เช่น พริกขี้หนู, กะเพรา, พลูด่าง, ลิ้นมังกร)",
      "plant_type": "ระบุประเภทระหว่าง 'ผักสวนครัว' หรือ 'ไม้ประดับ'",
      "health_analysis": "ประเมินสุขภาพพืชและหน้าดินจากภาพ (เช่น ดินแห้งหน้าแตก ใบเริ่มเหี่ยว หรือ สุขภาพดี ดินชื้นพอดี)",
      "pest_or_disease": "ระบุโรคพืชหรือศัตรูพืชที่พบเบื้องต้น (ถ้าไม่มีให้ระบุ 'ปกติไม่พบศัตรูพืช')",
      "water_interval_days": 3,
      "watering_instructions": "คำแนะนำการรดน้ำสั้นๆ (เช่น รดน้ำให้ชุ่มโคนต้นทุกๆ 3 วัน เช้าหรือเย็น)",
      "sunlight_requirement": "ความต้องการแสงแดด (เช่น แดดจัดครึ่งวัน, แดดรำไร หรือ เลี้ยงในร่มได้)",
      "care_tips": "เทคนิคการดูแลเพิ่มเติม เช่น การใส่ปุ๋ยบำรุง หรือการตัดแต่งกิ่ง"
    }

    ถ้าไม่มั่นใจว่าเป็นพืชชนิดใด ให้ระบุ plant_name เป็นชื่อกลุ่ม/ตระกูลที่ใกล้เคียงที่สุด และใส่เครื่องหมาย "(ไม่แน่ใจ)" ต่อท้าย แทนการเดาชื่อเจาะจงมั่ว ๆ

    หมายเหตุสำหรับค่า "water_interval_days": ต้องใส่เฉพาะตัวเลขจำนวนเต็มเท่านั้น (เช่น 1, 2, 3 หรือ 7)
    """

    # ไล่ลองหลายโมเดล ถ้าตัวไหนเจอ 503 (โหลดสูง) จะสลับไปตัวถัดไปอัตโนมัติ
    models_to_try = ["gemini-3.1-flash-lite", "gemini-2.5-flash-lite", "gemini-3-flash-preview"]
    response = None
    last_error = None

    for model_name in models_to_try:
        for attempt in range(2):  # ลอง 2 ครั้งต่อโมเดล
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=[image, prompt],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"
                    )
                )
                break  # สำเร็จแล้ว ออกจาก loop ครั้งนี้
            except Exception as e:
                last_error = e
                if "503" in str(e) or "UNAVAILABLE" in str(e):
                    time.sleep(3)
                    continue
                else:
                    raise  # error อื่นที่ไม่ใช่ 503 ให้โยนออกทันที
        if response is not None:
            break  # โมเดลนี้สำเร็จแล้ว ไม่ต้องลองโมเดลถัดไป

    if response is None:
        raise last_error

    return json.loads(response.text)


# -----------------------------------------------------------------------------
# 4. ส่วนการจัดวางหน้าจอ UI (Streamlit Layout)
# -----------------------------------------------------------------------------
st.title("🌱 Smart Plant Water Reminder")
st.subheader("ระบบประเมินสุขภาพและแจ้งเตือนการรดน้ำผักสวนครัว & ไม้ประดับ")
st.write("ถ่ายรูปหรืออัปโหลดรูปภาพกระถางต้นไม้/แปลงผัก เพื่อให้ AI วิเคราะห์การดูแลและตั้งเวลาเตือนรดน้ำ")

st.divider()

# -----------------------------------------------------------------------------
# 4.1 แบนเนอร์แจ้งเตือน — เช็คทุกครั้งที่เปิดหน้าเว็บว่ามีต้นไม้ถึงรอบรดน้ำหรือยัง
# -----------------------------------------------------------------------------
all_reminders = load_reminders()
today_date = datetime.now().date()
due_reminders = [
    r for r in all_reminders
    if datetime.fromisoformat(r["next_watering"]).date() <= today_date
]

if due_reminders:
    names = ", ".join(r["plant_name"] for r in due_reminders)
    st.error(f"🚨 **ถึงเวลารดน้ำแล้ว!** ต้นไม้ที่ต้องรดวันนี้: {names}")

# -----------------------------------------------------------------------------
# 4.2 รายการต้นไม้ที่บันทึกไว้ทั้งหมด พร้อมปุ่ม "รดน้ำแล้ว"
# -----------------------------------------------------------------------------
if all_reminders:
    with st.expander(f"📋 รายการต้นไม้ที่ติดตามอยู่ ({len(all_reminders)} ต้น)", expanded=bool(due_reminders)):
        for r in sorted(all_reminders, key=lambda x: x["next_watering"]):
            next_date = datetime.fromisoformat(r["next_watering"]).date()
            days_left = (next_date - today_date).days
            is_due = days_left <= 0

            col1, col2, col3 = st.columns([3, 2, 1.5])
            with col1:
                label = f"🔴 {r['plant_name']}" if is_due else f"🟢 {r['plant_name']}"
                st.write(f"**{label}** ({r['plant_type']})")
            with col2:
                if is_due:
                    st.write(f"⏰ ถึงกำหนดแล้ว ({next_date.strftime('%d/%m/%Y')})")
                else:
                    st.write(f"อีก {days_left} วัน ({next_date.strftime('%d/%m/%Y')})")
            with col3:
                if st.button("💧 รดแล้ว", key=f"water_{r['id']}"):
                    mark_watered(r["id"])
                    st.rerun()
                if st.button("🗑️", key=f"del_{r['id']}"):
                    delete_reminder(r["id"])
                    st.rerun()

st.divider()

# Sidebar: สำหรับกรอก API Key
with st.sidebar:
    st.header("⚙️ การตั้งค่า")
    api_key_input = st.text_input(
        "กรอก Gemini API Key:",
        type="password",
        help="ขอ API Key ฟรีได้ที่ https://aistudio.google.com"
    )
    # ดึงค่าจาก Environment Variable หรือ Secrets หากไม่ได้กรอกใน Sidebar
    api_key = api_key_input or os.environ.get("GEMINI_API_KEY") or st.secrets.get("GEMINI_API_KEY", "")

# เลือกวิธีป้อนรูปภาพ (อัปโหลด หรือ ใช้กล้องถ่าย)
input_option = st.radio(
    "เลือกวิธีการนำเข้ารถภาพ:",
    ["📤 อัปโหลดไฟล์รูปภาพ", "📷 ถ่ายรูปจากกล้อง"],
    horizontal=True
)

uploaded_file = None
if input_option == "📤 อัปโหลดไฟล์รูปภาพ":
    uploaded_file = st.file_uploader("เลือกรูปถ่ายต้นไม้/ผักสวนครัว (JPG, PNG)", type=["jpg", "jpeg", "png"])
else:
    uploaded_file = st.camera_input("ถ่ายรูปต้นไม้หรือแปลงผัก")

# เก็บผลวิเคราะห์ไว้ใน session_state เพื่อให้ปุ่ม "บันทึก" กดได้หลังวิเคราะห์เสร็จ
if "analysis_result" not in st.session_state:
    st.session_state.analysis_result = None
if "reminder_saved" not in st.session_state:
    st.session_state.reminder_saved = False

# -----------------------------------------------------------------------------
# 5. ส่วนการประมวลผลเมื่อมีรูปภาพป้อนเข้ามา
# -----------------------------------------------------------------------------
if uploaded_file is not None:
    image = Image.open(uploaded_file)
    st.image(image, caption="รูปภาพที่นำเข้า", use_container_width=True)

    if st.button("🔍 วิเคราะห์และคำนวณวันรดน้ำ", type="primary"):
        st.session_state.reminder_saved = False
        if not api_key:
            st.error("❌ กรุณากรอก Gemini API Key ที่ Sidebar ฝั่งซ้ายก่อนเริ่มใช้งาน")
        else:
            with st.spinner("🤖 AI กำลังสแกนความชื้นดิน สุขภาพใบ และประมวลผลคำแนะนำ..."):
                try:
                    result = analyze_plant(image, api_key)
                    st.session_state.analysis_result = result
                except Exception as e:
                    st.session_state.analysis_result = None
                    st.error(f"เกิดข้อผิดพลาดในการประมวลผล: {e}")

    result = st.session_state.analysis_result
    if result:
        today = datetime.now()
        interval_days = int(result.get("water_interval_days", 1))
        next_water_date = today + timedelta(days=interval_days)

        st.success("✅ วิเคราะห์ข้อมูลสำเร็จ!")
        st.divider()

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric(label="ชื่อพืช", value=result.get("plant_name", "ไม่ระบุ"))
        with col2:
            st.metric(label="ประเภท", value=result.get("plant_type", "ไม่ระบุ"))
        with col3:
            st.metric(label="รดน้ำทุกๆ", value=f"{interval_days} วัน")

        st.warning(
            f"📅 **กำหนดการรดน้ำครั้งถัดไป:** วันที่ {next_water_date.strftime('%d/%m/%Y')} "
            f"(อีก {interval_days} วันนับจากวันนี้)"
        )

        # ปุ่มบันทึกเข้าระบบติดตาม เพื่อให้ขึ้นแบนเนอร์แจ้งเตือนอัตโนมัติเมื่อถึงรอบ
        if st.session_state.reminder_saved:
            st.success("💾 บันทึกเข้าระบบติดตามแล้ว — จะมีแบนเนอร์แจ้งเตือนขึ้นเมื่อถึงรอบรดน้ำ")
        else:
            if st.button("💾 บันทึกต้นนี้เข้าระบบแจ้งเตือน"):
                add_reminder(
                    result.get("plant_name", "ไม่ระบุชื่อ"),
                    interval_days,
                    result.get("plant_type", "ไม่ระบุ"),
                )
                st.session_state.reminder_saved = True
                st.rerun()

        st.subheader("📋 รายละเอียดการวิเคราะห์สภาพพืช")
        st.write(f"**🧐 สุขภาพพืชและหน้าดิน:** {result.get('health_analysis')}")
        st.write(f"**🐛 โรคพืช/ศัตรูพืชที่พบ:** {result.get('pest_or_disease')}")
        st.write(f"**💧 วิธีการรดน้ำ:** {result.get('watering_instructions')}")
        st.write(f"**☀️ แสงแดดที่ต้องการ:** {result.get('sunlight_requirement')}")
        st.write(f"**💡 คำแนะนำเพิ่มเติม:** {result.get('care_tips')}")
