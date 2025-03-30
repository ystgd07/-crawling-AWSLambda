import requests
import psycopg2
import os
import datetime

def lambda_handler(event, context):
    url = "https://www.wanted.co.kr/api/v4/jobs"
    params = {
        "limit": 100,
        "offset": 0,
        "country": "kr"
    }
    headers = {"User-Agent": "Mozilla/5.0"}

    # API 요청
    res = requests.get(url, params=params, headers=headers)
    jobs = res.json().get("data", [])

    # DB 연결 (환경변수로부터 정보 로드)
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
        detailurl = f"https://www.wanted.co.kr/wd/{job_id}"

        # due_time 추출: 문자열이 제공되면 datetime으로 파싱, 없으면 None
        due_time_str = job.get("due_time")
        if due_time_str:
            try:
                # due_time이 ISO 8601 형식이라고 가정 (예: "2025-04-08")
                due_time_dt = datetime.datetime.fromisoformat(due_time_str)
            except Exception as e:
                # 파싱 실패 시 None으로 처리
                due_time_dt = None
        else:
            due_time_dt = None

        # INSERT: 중복 방지를 위해 (source, external_id) UNIQUE 제약을 사용
        cursor.execute("""
            INSERT INTO jobs (external_id, title, company, annual_from, annual_to, source, detailurl, due_time)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (external_id) DO NOTHING
        """, (job_id, title, company, annual_from, annual_to, source, detailurl, due_time_dt))

    conn.commit()
    cursor.close()
    conn.close()

    return {"statusCode": 200, "body": f"{len(jobs)} jobs inserted."}
