from flask import Flask, render_template, request, jsonify
from openai import OpenAI
import os
from datetime import datetime
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")

conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True

with conn.cursor() as cur:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

app = Flask(__name__)

# Use environment variable (recommended)
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

    if user_email and user_email != "anonymous":
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (email) VALUES (%s);",
                    (user_email,)
                )
        except Exception as e:
            print("DB insert error:", e)

    # (Optional) simple logging (no DB yet)
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)