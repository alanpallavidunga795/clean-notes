import traceback
from flask import Flask, render_template, request, jsonify, Response
from openai import OpenAI
import os
from datetime import datetime
import psycopg2
import smtplib
from email.mime.text import MIMEText
from dotenv import load_dotenv
from functools import wraps
import re

load_dotenv()

app = Flask(__name__)

# ===== DATABASE SETUP =====
DATABASE_URL = os.getenv("DATABASE_URL")

conn = None

if DATABASE_URL:
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True

        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    email TEXT
                );
            """)

            cur.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS last_used TIMESTAMP;
            """)

            cur.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS request_count INTEGER DEFAULT 1;
            """)

            cur.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'tool';
            """)

            cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'users_email_key'
                    ) THEN
                        ALTER TABLE users
                        ADD CONSTRAINT users_email_key UNIQUE (email);
                    END IF;
                END
                $$;
            """)

    except Exception as e:
        print("Database connection failed:", e)


# ===== OPENAI SETUP =====
api_key = os.getenv("OPENAI_API_KEY")

if not api_key:
    raise ValueError("OPENAI_API_KEY is not set")

client = OpenAI(api_key=api_key)


# ===== EMAIL ALERT FUNCTION =====
def send_email_alert(source, email, message):

    sender_email = os.getenv("EMAIL_SENDER")
    sender_password = os.getenv("EMAIL_PASSWORD")

    if not sender_email or not sender_password:
        return

    recipient = "alan.bellinger@aiagentforhealth.com"

    msg = MIMEText(f"""
Type: {source}
Time: {datetime.now()}
Email: {email if email else "Not provided"}

Message:
{message}
""")

    msg["Subject"] = f"CleanNotes DB Entry: {source}"
    msg["From"] = sender_email
    msg["To"] = recipient

    try:
        with smtplib.SMTP_SSL("smtp.hostinger.com", 465) as server:
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, recipient, msg.as_string())
    except Exception as e:
        print("Email alert failed:", e)


# ===== 🔒 SMART CLINICAL INPUT FILTER =====
def is_clinical_input(text):
    text = text.lower().strip()

    if len(text.split()) < 3:
        return False

    clinical_terms = [
        "pain", "fever", "cough", "fatigue", "nausea", "vomiting",
        "anxiety", "depression", "injury", "infection", "swelling",
        "headache", "dizziness", "shortness of breath", "chest pain"
    ]

    context_terms = [
        "patient", "pt", "male", "female", "yo", "years old",
        "history", "hx", "diagnosed", "presents", "reports", "with"
    ]

    has_clinical = any(term in text for term in clinical_terms)
    has_context = any(term in text for term in context_terms)

    if has_clinical and has_context:
        return True

    if sum(term in text for term in clinical_terms) >= 2:
        return True

    return False


# ===== PROMPT TEMPLATE (FIXED) =====
def build_prompt(user_input):
    return f"""
You are a clinical documentation assistant.

Your task is to generate structured clinical documentation from the provided input.

================================
INPUT VALIDATION
================================

If the input is not clearly a clinical case:
RETURN EXACTLY:
Clinical information is required for appropriate output.

================================
ZERO-INFERENCE RULE (CRITICAL)
================================

You are strictly prohibited from adding ANY information not explicitly present.

DO NOT invent:
- duration
- severity
- frequency
- progression
- timeline details

If not stated → DO NOT include it.

Missing details MUST appear ONLY in "Missing Information".

================================
CRITICAL DIFFERENTIATION RULE
================================

You MUST generate THREE "Missing Information" sections that are:

- Conceptually distinct
- Written using different reasoning perspectives
- Using different wording and structure
- NOT overlapping in meaning
- NOT paraphrased versions of each other

Each section MUST use a DIFFERENT lens:

1. SOAP NOTE → Diagnostic uncertainty
   (What prevents confirming a diagnosis?)

2. BULLET SUMMARY → Measurable data gaps
   (What quantifiable data is missing?)

3. PARAGRAPH SUMMARY → Contextual gaps
   (What background or situational info is missing?)

If overlap occurs:
You MUST reframe the item so it becomes unique.

================================
OUTPUT STRUCTURE
================================

### SOAP NOTE
#### Subjective
Paragraph using ONLY stated information.

#### Objective
Paragraph using ONLY stated information.

#### Assessment
Cautious interpretation WITHOUT adding facts.

#### Plan
Next steps ONLY if justified.

#### Missing Information:
3–5 diagnostic uncertainties required to clarify the case.

---

### BULLET SUMMARY
- Facts only

#### Missing Information:
3–5 measurable or quantifiable missing data points 
(e.g., vitals, lab values, durations, frequencies).

---

### PARAGRAPH SUMMARY
One clean paragraph.

#### Missing Information:
3–5 contextual or background gaps 
(e.g., history, environment, timeline clarity, prior care).

---

================================
HARD CONSTRAINTS
================================

- NO hallucinations
- NO invented timelines
- NO assumptions
- DO NOT add new facts
- DO NOT reuse or reword items across sections
- EACH section must feel independently reasoned

================================
INPUT:
{user_input}

OUTPUT:
Return ONLY the formatted output.
"""


# ===== CLEAN STRUCTURE FIX =====
def normalize_output(text):
    if not text:
        return text

    text = text.replace('--------------------------------', '---')
    parts = text.split('---')

    if len(parts) >= 3:
        return '---'.join([p.strip() for p in parts[:3]])

    return text


# ===== CONTACT ROUTE =====
@app.route("/contact", methods=["POST"])
def contact():

    data = request.get_json(silent=True)

    if not data:
        return jsonify({"error": "No JSON received"}), 400

    email = data.get("email", "")
    message = data.get("message", "")

    if not message.strip():
        return jsonify({"error": "Missing message"}), 400

    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO users (email, source, last_used, request_count)
                    VALUES (%s, %s, NOW(), 1)
                    ON CONFLICT (email)
                    DO UPDATE SET
                        last_used = NOW(),
                        request_count = users.request_count + 1,
                        source = EXCLUDED.source;
                """, (email or "anonymous", "contact"))
        except Exception as e:
            print("DB contact error:", e)

    send_email_alert("contact", email, message)

    return jsonify({"status": "success"})


# ===== ROUTES =====
@app.route("/")
def landing():
    return render_template("index.html")

@app.route("/app")
def app_page():
    return render_template("app.html")


@app.route("/generate", methods=["POST"])
def generate():

    data = request.get_json(silent=True)

    if not data:
        return jsonify({"error": "No JSON received"}), 400

    user_input = data.get("input", "")
    user_email = data.get("email", "anonymous")

    if not user_input.strip():
        return jsonify({"error": "No input provided"}), 400

    send_email_alert("tool", user_email, user_input)

    if not is_clinical_input(user_input):
        return jsonify({"result": "Clinical information is required for appropriate output."})

    if conn and user_email != "anonymous":
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO users (email, source, last_used, request_count)
                    VALUES (%s, %s, NOW(), 1)
                    ON CONFLICT (email)
                    DO UPDATE SET
                        last_used = NOW(),
                        request_count = users.request_count + 1;
                """, (user_email, "tool"))
        except Exception as e:
            print("DB error:", e)

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Strict clinical formatter."},
                {"role": "user", "content": build_prompt(user_input)}
            ],
            temperature=0.2
        )

        output = response.choices[0].message.content
        output = normalize_output(output)

        return jsonify({"result": output})

    except Exception as e:
        print("OPENAI ERROR:", traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@app.route("/test-email")
def test_email():
    send_email_alert("test", "test@local", "SMTP working")
    return "Email sent"


# ===== BASIC AUTH =====
ADMIN_USERNAME = os.getenv("DB_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("DB_ADMIN_PASSWORD", "change-this-password")

def check_auth(username, password):
    return username == ADMIN_USERNAME and password == ADMIN_PASSWORD

def authenticate():
    return Response(
        "Authentication required", 401,
        {"WWW-Authenticate": 'Basic realm="Login Required"'}
    )

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated


@app.route("/reset-db")
@requires_auth
def reset_db():
    if not conn:
        return "Database not connected"

    try:
        with conn.cursor() as cur:
            cur.execute("""
                DO $$ DECLARE
                    r RECORD;
                BEGIN
                    FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public')
                    LOOP
                        EXECUTE 'TRUNCATE TABLE ' || quote_ident(r.tablename) || ' RESTART IDENTITY CASCADE';
                    END LOOP;
                END $$;
            """)

        return "Database cleared"

    except Exception as e:
        return f"Error clearing DB: {e}"


@app.route("/admin/users")
@requires_auth
def admin_users():
    if not conn:
        return "Database not connected"

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    ROW_NUMBER() OVER (ORDER BY last_used DESC NULLS LAST) AS row_num,
                    email, 
                    source, 
                    last_used, 
                    request_count
                FROM users;
            """)
            rows = cur.fetchall()

        html = "<h2>Users</h2><table border='1' cellpadding='6'>"
        html += "<tr><th>#</th><th>Email</th><th>Source</th><th>Last Used</th><th>Requests</th></tr>"

        for r in rows:
            html += f"<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td><td>{r[4]}</td></tr>"

        html += "</table>"
        return html

    except Exception as e:
        return f"Error: {e}"


# ===== RUN =====
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=True, use_reloader=False)