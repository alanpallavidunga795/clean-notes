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
        print("Email credentials not set.")
        return

    recipient = "alanadrift@gmail.com"

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


# ===== PROMPT TEMPLATE =====
def build_prompt(user_input):
    return f"""
You are a clinical documentation assistant supporting healthcare professionals.

You MUST generate:
1. SOAP NOTE
2. BULLET SUMMARY
3. PARAGRAPH SUMMARY

STRICT FORMAT:

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
    data = request.get_json(force=True)
    user_input = data.get("input", "")
    user_email = data.get("email", "anonymous")

    if not user_input.strip():
        return jsonify({"error": "No input provided"}), 400

    if conn and user_email != "anonymous":
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

    send_email_alert("tool", user_email, user_input)

    try:
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

    except Exception as e:
        print("OPENAI FAILURE:", e)
        return jsonify({"error": str(e)}), 500


@app.route("/test-email")
def test_email():
    send_email_alert("test", "test@local", "SMTP working")
    return "Email sent"


@app.route("/admin/users")
def admin_users():
    return "OK"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)