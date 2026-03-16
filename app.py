from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from dotenv import load_dotenv
load_dotenv()
from functools import wraps
from datetime import datetime
import google.generativeai as genai
import base64
import os
import json
import uuid
import threading

app = Flask(__name__)
ai_lock = threading.Lock()
app.secret_key = "campus-secret-key-2025"

UPLOAD_FOLDER = os.path.join("static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

USERS = {
    "student1": {"password": "pass123", "role": "student", "name": "Arjun Sharma",  "email": "arjun@campus.edu"},
    "student2": {"password": "pass123", "role": "student", "name": "Priya Verma",   "email": "priya@campus.edu"},
    "admin":    {"password": "admin123","role": "admin",   "name": "Dr. Mehta",     "email": "admin@campus.edu"},
}

FOUND_ITEMS = [
    {"id": "f1", "item": "Black leather wallet",        "category": "Wallet/Purse", "location": "Library entrance",      "date": "2025-01-15 10:30", "status": "unclaimed", "submitted_by": "staff"},
    {"id": "f2", "item": "Blue JBL earphones",          "category": "Earphones",    "location": "Canteen, table 5",      "date": "2025-01-14 14:00", "status": "unclaimed", "submitted_by": "student1"},
    {"id": "f3", "item": "Student ID card (Riya S.)",   "category": "ID Card",      "location": "Lecture Hall A, row 3", "date": "2025-01-14 16:15", "status": "unclaimed", "submitted_by": "staff"},
    {"id": "f4", "item": "Anatomy textbook",            "category": "Books/Notes",  "location": "Library 2nd floor",     "date": "2025-01-15 09:00", "status": "unclaimed", "submitted_by": "student2"},
    {"id": "f5", "item": "Set of 3 keys, blue keyring", "category": "Keys",         "location": "Hostel Block B",        "date": "2025-01-15 08:00", "status": "unclaimed", "submitted_by": "staff"},
]

LOST_QUERIES  = []
ISSUE_REPORTS = []

SYSTEM_LOST = """You are an AI assistant for a university smart campus lost & found system.
Analyze the lost item query and match it with found items database.
Return ONLY valid JSON, no extra text, no markdown:
{"item_type":"...","possible_match":"...","match_confidence":"High|Medium|Low|None","suggested_next_step":"...","notes":"..."}"""

SYSTEM_ISSUE = """You are an AI assistant for a university smart campus issue reporting system.
Analyze the campus issue description and generate a structured report.
Return ONLY valid JSON, no extra text, no markdown:
{"issue_type":"...","severity":"High|Medium|Low","description":"...","probable_location":"...","suggested_action":"...","department":"...","estimated_resolution":"..."}"""

def get_gemini():
    api_key = os.environ.get("GEMINI_API_KEY") or session.get("gemini_key")
    if not api_key:
        return None
    genai.configure(api_key=api_key)
    return genai.GenerativeModel("models/gemini-flash-latest")

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("role") != "admin":
            flash("Admin access required.", "danger")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated

def parse_json(text):
    clean = text.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(clean)

@app.route("/")
def index():
    return redirect(url_for("dashboard") if "username" in session else url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "").strip()
        user = USERS.get(u)
        if user and user["password"] == p:
            session.update({"username": u, "role": user["role"], "name": user["name"]})
            flash(f"Welcome, {user['name']}!", "success")
            return redirect(url_for("admin_dashboard") if user["role"] == "admin" else url_for("dashboard"))
        flash("Invalid username or password.", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/set-api-key", methods=["POST"])
@login_required
def set_api_key():
    key = request.form.get("api_key", "").strip()
    if key:
        session["gemini_key"] = key
        return jsonify({"status": "ok"})
    return jsonify({"status": "error", "message": "Invalid key"}), 400

@app.route("/dashboard")
@login_required
def dashboard():
    my_queries = [q for q in LOST_QUERIES if q["submitted_by"] == session["username"]]
    my_issues  = [r for r in ISSUE_REPORTS if r["submitted_by"] == session["username"]]
    return render_template("dashboard.html",
        found_items      = FOUND_ITEMS,
        lost_queries     = my_queries,
        my_issues        = my_issues,
        all_lost_queries = LOST_QUERIES,
    )

@app.route("/lost-item", methods=["GET", "POST"])
@login_required
def lost_item():
    result = None
    if request.method == "POST":
        desc       = request.form.get("description", "").strip()
        cat        = request.form.get("category", "")
        loc        = request.form.get("location", "")
        image_file = request.files.get("image")
        saved_img  = None
        ext        = ""
        model = get_gemini()
        if not model:
            flash("Pehle Gemini API key set karo!", "warning")
            return redirect(url_for("lost_item"))
        if image_file and image_file.filename:
            ext       = image_file.filename.rsplit(".", 1)[-1].lower()
            filename  = f"lost_{uuid.uuid4().hex[:8]}.{ext}"
            save_path = os.path.join(UPLOAD_FOLDER, filename)
            image_file.save(save_path)
            saved_img = filename
        found_summary = "\n".join([
            f"- [{f['id']}] {f['item']} [{f['category']}] at {f['location']} on {f['date']} — {f['status']}"
            for f in FOUND_ITEMS if f["status"] == "unclaimed"
        ])
        if not ai_lock.acquire(blocking=False):
            flash("⏳ AI abhi busy hai, 5 second baad dobara try karo!", "warning")
            return redirect(url_for("lost_item"))
        try:
            if saved_img:
                img_data = open(os.path.join(UPLOAD_FOLDER, saved_img), "rb").read()
                img_part = {"mime_type": f"image/{'jpeg' if ext in ('jpg','jpeg') else ext}", "data": img_data}
                prompt   = [img_part, f"""{SYSTEM_LOST}

Lost item (image attached):
Category: {cat or 'Not specified'}
Last seen: {loc or 'Not specified'}
Description: {desc or 'See uploaded image'}

Found items in database:
{found_summary}"""]
            else:
                prompt = f"""{SYSTEM_LOST}

Lost item:
Category: {cat or 'Not specified'}
Last seen: {loc or 'Not specified'}
Description: {desc or 'Not provided'}

Found items in database:
{found_summary}"""
            response = model.generate_content(prompt)
            result   = parse_json(response.text)
            LOST_QUERIES.append({
                "id":           str(uuid.uuid4())[:8],
                "description":  desc,
                "category":     cat,
                "location":     loc,
                "image":        saved_img,
                "result":       result,
                "submitted_by": session["username"],
                "student_name": session["name"],
                "date":         datetime.now().strftime("%Y-%m-%d %H:%M"),
                "status":       "open"
            })
        except Exception as e:
            flash(f"AI Error: {str(e)}", "danger")
        finally:
            ai_lock.release()
    return render_template("lost_item.html", result=result, found_items=FOUND_ITEMS)

@app.route("/report-issue", methods=["GET", "POST"])
@login_required
def report_issue():
    result = None
    if request.method == "POST":
        desc  = request.form.get("description", "").strip()
        loc   = request.form.get("location", "")
        model = get_gemini()
        if not model:
            flash("Pehle Gemini API key set karo!", "warning")
            return redirect(url_for("report_issue"))
        image_file = request.files.get("image")
        saved_img  = None
        if not ai_lock.acquire(blocking=False):
            flash("⏳ AI abhi busy hai, 5 second baad dobara try karo!", "warning")
            return redirect(url_for("report_issue"))
        try:
            if image_file and image_file.filename:
                ext       = image_file.filename.rsplit(".", 1)[-1].lower()
                filename  = f"{uuid.uuid4().hex[:8]}.{ext}"
                save_path = os.path.join(UPLOAD_FOLDER, filename)
                image_file.save(save_path)
                saved_img = filename
                img_data  = open(save_path, "rb").read()
                img_part  = {"mime_type": f"image/{'jpeg' if ext in ('jpg','jpeg') else ext}", "data": img_data}
                prompt    = [img_part, f"{SYSTEM_ISSUE}\n\nLocation: {loc or 'Not specified'}\nDescription: {desc or 'See image'}"]
                response  = model.generate_content(prompt)
            else:
                prompt   = f"{SYSTEM_ISSUE}\n\nLocation: {loc or 'Not specified'}\nDescription: {desc}"
                response = model.generate_content(prompt)
            result = parse_json(response.text)
            ISSUE_REPORTS.append({
                "id":           str(uuid.uuid4())[:8],
                "description":  desc,
                "location":     loc,
                "image":        saved_img,
                "result":       result,
                "submitted_by": session["username"],
                "date":         datetime.now().strftime("%Y-%m-%d %H:%M"),
                "status":       "open"
            })
        except Exception as e:
            flash(f"AI Error: {str(e)}", "danger")
        finally:
            ai_lock.release()
    return render_template("report_issue.html", result=result)

@app.route("/admin")
@login_required
@admin_required
def admin_dashboard():
    stats = {
        "total_users":   len(USERS),
        "found_items":   len(FOUND_ITEMS),
        "lost_queries":  len(LOST_QUERIES),
        "issue_reports": len(ISSUE_REPORTS),
        "open_issues":   len([r for r in ISSUE_REPORTS if r.get("status") == "open"]),
        "resolved":      len([r for r in ISSUE_REPORTS if r.get("status") == "resolved"]),
        "high_severity": len([r for r in ISSUE_REPORTS if r.get("result", {}).get("severity") == "High"]),
    }
    return render_template("admin.html", stats=stats,
        found_items=FOUND_ITEMS, lost_queries=LOST_QUERIES,
        issue_reports=ISSUE_REPORTS, users=USERS)

@app.route("/admin/update-status", methods=["POST"])
@login_required
@admin_required
def update_status():
    t, i, s = request.form.get("type"), request.form.get("id"), request.form.get("status")
    data_map = {"found": FOUND_ITEMS, "issue": ISSUE_REPORTS, "lost": LOST_QUERIES}
    for item in data_map.get(t, []):
        if item["id"] == i:
            item["status"] = s
    return jsonify({"status": "ok"})

@app.route("/admin/add-found", methods=["POST"])
@login_required
@admin_required
def add_found_item():
    FOUND_ITEMS.append({
        "id":           f"f{len(FOUND_ITEMS)+1}",
        "item":         request.form.get("item", ""),
        "category":     request.form.get("category", "Other"),
        "location":     request.form.get("location", ""),
        "date":         datetime.now().strftime("%Y-%m-%d %H:%M"),
        "status":       "unclaimed",
        "submitted_by": session["username"],
    })
    flash("Found item added!", "success")
    return redirect(url_for("admin_dashboard"))

if __name__ == "__main__":
    app.run(debug=True, port=5000)