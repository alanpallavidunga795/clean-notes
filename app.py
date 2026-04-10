from flask import Flask, render_template, request, jsonify, Response
from openai import OpenAI
import os
from datetime import datetime
import psycopg2
import smtplib
from email.mime.text import MIMEText

app = Flask(__name__)

# ===== DATABASE SETUP =====
DATABASE_URL = os.getenv("DATABASE_URL")

conn = None

if DATABASE_URL:
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True

        with conn.cursor() as cur:
            # Base table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    email TEXT
                );
            """)

            # Safe schema upgrades
            cur.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS last_used TIMESTAMP;
            """)

            cur.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS request_count INTEGER DEFAULT 1;
            """)

            # Ensure UNIQUE email constraint (required for ON CONFLICT)
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
    raise ValueError("OPENAI_API_KEY is not set in environment variables")

client = OpenAI(api_key=api_key)


# ===== EMAIL ALERT FUNCTION (HOSTINGER SMTP) =====
def send_email_alert(source, email, message):
    """
    Sends alert when DB receives new entry
    """

    sender_email = os.getenv("EMAIL_SENDER")
    sender_password = os.getenv("EMAIL_PASSWORD")

    if not sender_email or not sender_password:
        print("Email credentials not set.")
        return

    recipient = "alanadrift@gmail.com"

    subject = f"CleanNotes DB Entry: {source}"

    body = f"""
New entry recorded in CleanNotes system:

Type: {source}
Time: {datetime.now()}

Email: {email}

Message:
{message}
"""

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = recipient

    try:
        with smtplib.SMTP_SSL("smtp.hostinger.com", 465) as server:
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, recipient, msg.as_string())

    except Exception as e:
        print("Email alert failed:", e)


# ===== PROMPT TEMPLATE =====
def build_prompt(user_input):
    return f"""
You are a clinical documentation assistant supporting healthcare professionals.

Your task is to convert raw clinical notes into structured documentation.

You MUST generate:
1. SOAP NOTE
2. BULLET SUMMARY
3. PARAGRAPH SUMMARY

CORE RULES:
- Do NOT invent clinical data
- Use only provided information
- Be concise and professional

FORMAT MUST BE STRICT:

### SOAP NOTE
#### Subjective
...
#### Objective
...
#### Assessment
...
#### Plan
...
#### Missing Information:
- ...

---

### BULLET SUMMARY
- ...
#### Missing Information:
- ...

---

### PARAGRAPH SUMMARY
...
#### Missing Information:
- ...

INPUT:
{user_input}

Return ONLY structured output.
"""


# ===== ROUTES =====
@app.route("/")
def landing():
    return render_template("index.html")


@app.route("/app")
def app_page():
    return render_template("app.html")


@app.route("/generate", methods=["POST"])
def generate():
    data = request.json
    user_input = data.get("input", "")
    user_email = data.get("email", "anonymous")

    if not user_input.strip():
        return jsonify({"error": "No input provided"}), 400

    # ===== DB LOGGING =====
    if conn and user_email and user_email != "anonymous":
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO users (email, last_used, request_count)
                    VALUES (%s, NOW(), 1)
                    ON CONFLICT (email)
                    DO UPDATE SET
                        last_used = NOW(),
                        request_count = users.request_count + 1;
                """, (user_email,))
        except Exception as e:
            print("DB error:", e)

    @app.route("/test-email")
    def test_email():
        try:
            send_email_alert(
                source="manual-test",
                email="test@local",
                message="This is a test email from CleanNotes SMTP pipeline."
            )
            return "Email sent (check inbox)"
        except Exception as e:
            return f"Email failed: {e}"

    # ===== EMAIL ALERT (TO YOU) =====
    send_email_alert("tool", user_email, user_input)

    print(f"[{datetime.now()}] Request from: {user_email}")

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a clinical documentation assistant."},
            {"role": "user", "content": build_prompt(user_input)}
        ],
        temperature=0.2
    )

    output = response.choices[0].message.content

    return jsonify({"result": output})


@app.route("/admin/users", methods=["GET"])
def admin_users():
    if conn is None:
        return "Database not connected"

    auth = request.authorization
    admin_password = os.getenv("ADMIN_PASSWORD")

    if not auth or auth.password != admin_password:
        return Response(
            "Login required",
            401,
            {"WWW-Authenticate": 'Basic realm="Login Required"'}
        )

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT email, last_used, request_count
                FROM users
                ORDER BY last_used DESC;
            """)
            rows = cur.fetchall()

        html = "<h2>Stored Users</h2><table border='1' cellpadding='6'>"
        html += "<tr><th>Email</th><th>Last Used</th><th>Requests</th></tr>"

        for r in rows:
            html += f"<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td></tr>"

        html += "</table>"
        return html

    except Exception as e:
        return f"Error: {e}"


# ===== RUN APP =====
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)