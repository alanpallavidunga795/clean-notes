from flask import Flask, render_template, request, jsonify
from openai import OpenAI
import os
from datetime import datetime
import psycopg2

app = Flask(__name__)

# ===== DATABASE SETUP =====
# ===== DATABASE SETUP =====
DATABASE_URL = os.getenv("DATABASE_URL")

conn = None

if DATABASE_URL:
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True

        with conn.cursor() as cur:
            # Create base table if not exists
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    email TEXT
                );
            """)

            # Add missing columns safely
            cur.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS last_used TIMESTAMP;
            """)

            cur.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS request_count INTEGER DEFAULT 1;
            """)

            # 🔥 CRITICAL FIX: ensure email is UNIQUE
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


# ===== PROMPT TEMPLATE =====
def build_prompt(user_input):
    return f"""
You are a clinical documentation assistant supporting healthcare professionals (doctors, nurses, therapists, and counselors).

Your task is to convert raw clinical notes into structured, professional clinical documentation.

You MUST generate all three output formats for every input:
1. SOAP NOTE
2. BULLET SUMMARY
3. PARAGRAPH SUMMARY

CORE RULES:
- Do NOT invent clinical data
- Do NOT diagnose beyond provided information
- Use clear, concise, professional clinical language
- Preserve all relevant details
- Explicitly identify missing information

FORMAT DEFINITIONS:

SOAP NOTE:
- Subjective: patient-reported
- Objective: measurable/observed only
- Assessment: cautious synthesis, non-diagnostic if uncertain
- Plan: suggest further evaluation only if needed

Do NOT put a colon after SOAP categories in output.
Make SOAP categories bold.

BULLET SUMMARY:
- Only factual extracted data
- One fact per bullet
- No interpretation

PARAGRAPH SUMMARY:
- Clean clinical paragraph
- No bullets or headers
- No added information

MISSING INFORMATION:
- Include after EACH format
- Tailor depth per format
- Do NOT repeat identical lists

You MUST follow this STRICT OUTPUT FORMAT:

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

Do NOT vary formatting. Output MUST follow the template every time.

INPUT:
{user_input}

Return ONLY the structured output.
Do NOT include redundant information.
"""


# ===== ROUTES =====
@app.route("/")
def home():
    return render_template("clean-notes.html")


@app.route("/generate", methods=["POST"])
def generate():
    data = request.json
    user_input = data.get("input", "")

    if not user_input.strip():
        return jsonify({"error": "No input provided"}), 400

    user_email = data.get("email", "anonymous")

    # Save email usage to database
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

    # Logging
    print(f"[{datetime.now()}] Request from: {user_email}")

    prompt = build_prompt(user_input)

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a clinical documentation assistant."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.2
    )

    output = response.choices[0].message.content

    return jsonify({"result": output})


@app.route("/admin/users", methods=["GET"])
def admin_users():
    if conn is None:
        return "Database not connected"

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