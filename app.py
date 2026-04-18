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
    if not email or email == "anonymous":
        return

    sender_email = os.getenv("EMAIL_SENDER")
    sender_password = os.getenv("EMAIL_PASSWORD")

    if not sender_email or not sender_password:
        return

    recipient = "alan.bellinger@aiagentforhealth.com"

    msg = MIMEText(f"""
Type: {source}
Time: {datetime.now()}
Email: {email}

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


# ===== PROMPT TEMPLATE (UNCHANGED) =====
def build_prompt(user_input):
    return f"""
You are a clinical documentation assistant.

Your task is to generate structured clinical documentation from the provided input.

CORE REQUIREMENT (CRITICAL):
You MUST generate THREE sections, each containing a "Missing Information" subsection.

These three "Missing Information" subsections MUST follow these rules:

1. They MUST be semantically distinct (different angles of missing data).
2. They MUST NOT reuse wording, phrasing, or sentence structure across sections.
3. They MUST NOT be paraphrases of each other.
4. Each list MUST be generated using a different reasoning lens:
     - SOAP → clinical diagnostic uncertainty
     - Bullet Summary → objective/measurable missing data
     - Paragraph Summary → contextual/background gaps
5. If overlap in concept is unavoidable, you MUST:
    - Change framing
    - Change terminology
    - Change sentence structure
    - Change level of abstraction

Failure to differentiate these will result in an incorrect output.

OUTPUT STRUCTURE (STRICT)
Generate EXACTLY three sections in this order:

### SOAP NOTE
#### Subjective
Write as a paragraph.

#### Objective
Write as a paragraph.

#### Assessment
Write as a paragraph, and provide cautious clinical interpretation.

#### Plan
Write as a paragraph, and list next steps if appropriate.

#### Missing Information:
Write 3–5 items focusing on diagnostic uncertainties or clinical unknowns.
These should reflect what a clinician still needs to confirm a diagnosis.
---
### BULLET SUMMARY
Extract facts only (one per bullet)
No interpretation

#### Missing Information:
Write 3–5 items focusing ONLY on quantifiable, measurable, or documentable data that is absent
(e.g., vitals, lab values, duration, frequency, scales, test results).
---
### PARAGRAPH SUMMARY
Write ONE clean, cohesive paragraph summarizing the case.

#### Missing Information:
Write 3–5 items focusing on contextual gaps, such as:
- history
- environment
- psychosocial factors
- timeline clarity
- prior treatment context
---
HARD CONSTRAINTS:
- DO NOT repeat ideas across "Missing Information" sections
- DO NOT reuse wording or phrasing across sections
- DO NOT generate templated or generic statements
- ALL content MUST be derived from the specific input
- Each section MUST feel independently reasoned

INPUT:
{user_input}

OUTPUT RULE:
Return ONLY the formatted output.
"""


# ===== CLEAN STRUCTURE FIX (CRITICAL) =====
def normalize_output(text):
    if not text:
        return text

    # Force correct separators (this is the real fix)
    text = text.replace('--------------------------------', '---')

    # Ensure exactly 3 sections
    parts = text.split('---')

    if len(parts) >= 3:
        return '---'.join([p.strip() for p in parts[:3]])

    return text

@app.route("/contact", methods=["POST"])
def contact():

    data = request.get_json(silent=True)

    if not data:
        return jsonify({"error": "No JSON received"}), 400

    email = data.get("email", "")
    message = data.get("message", "")

    if not email or not message.strip():
        return jsonify({"error": "Missing fields"}), 400

    # ===== DB ENTRY (CONTACT SOURCE) =====
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
                """, (email, "contact"))
        except Exception as e:
            print("DB contact error:", e)

    # ===== EMAIL ALERT (CONTACT SOURCE) =====
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

    # DB
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

    # EMAIL
    send_email_alert("tool", user_email, user_input)

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Strict clinical formatter."},
                {"role": "user", "content": build_prompt(user_input)}
            ],
            temperature=0.3
        )

        output = response.choices[0].message.content

        # 🔥 REAL FIX (STRUCTURE, NOT CONTENT)
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


# ===== SECURED ADMIN ROUTE =====

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