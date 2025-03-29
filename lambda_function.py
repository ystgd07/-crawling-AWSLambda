import requests
import psycopg2
import os

def lambda_handler(event, context):
    url = "https://www.wanted.co.kr/api/v4/jobs"
    params = {
        "limit": 10,
        "offset": 0,
        "country": "kr"
    }
    headers = {"User-Agent": "Mozilla/5.0"}

    res = requests.get(url, params=params, headers=headers)
    jobs = res.json().get("data", [])

    conn = psycopg2.connect(
        host=os.environ["DB_HOST"],
        database=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        port=5432
    )
    cursor = conn.cursor()

    for job in jobs:
        job_id = job["id"]
        title = job["position"]
        company = job["company"]["name"]
        annual_from = job.get("annual_from")
        annual_to = job.get("annual_to")
        source = "wanted"

        cursor.execute("""
            INSERT INTO jobs (external_id, title, company, annual_from, annual_to, source)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (external_id) DO NOTHING
        """, (job_id, title, company, annual_from, annual_to, source))

    conn.commit()
    cursor.close()
    conn.close()

    return {"statusCode": 200, "body": f"{len(jobs)} jobs inserted."}