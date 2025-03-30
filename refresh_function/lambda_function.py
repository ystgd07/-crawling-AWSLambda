import psycopg2
import requests
import os
from datetime import datetime, timedelta

def lambda_handler(event, context):
    """
    AWS Lambda 진입점 함수
    """
    try:
        refresh_jobs()
        return {
            "statusCode": 200,
            "body": "Job refresh completed successfully."
        }
    except Exception as e:
        print(f"Error in lambda_handler: {e}")
        return {
            "statusCode": 500,
            "body": f"Error occurred: {str(e)}"
        }

def validate_job_via_api(job):
    """
    (옵션) API 재검증을 통해 상태를 판단하는 함수.
    이 예에서는 due_time이 있다면 그 값을, 없으면 posted_date를 기준으로 처리합니다.
    """
    try:
        api_url = f"https://www.wanted.co.kr/api/v4/jobs/{job['external_id']}"
        response = requests.get(api_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if response.status_code == 200:
            data = response.json()
            api_due_time = data.get("due_time")
            if api_due_time:
                api_due_dt = datetime.fromisoformat(api_due_time)
                if datetime.now() > api_due_dt:
                    return "closed"
                else:
                    return "active"
            else:
                api_posted_time = data.get("posted_date")
                if api_posted_time:
                    posted_dt = datetime.fromisoformat(api_posted_time)
                    if datetime.now() - posted_dt > timedelta(days=30):
                        return "closed"
                    else:
                        return "open_ended"
                else:
                    return job["status"]
        else:
            return job["status"]
    except Exception as e:
        print(f"API 검증 중 오류 발생 (external_id {job['external_id']}): {e}")
        return job["status"]

def refresh_jobs():
    conn = psycopg2.connect(
        host=os.environ["DB_HOST"],
        database=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        port=5432
    )
    cursor = conn.cursor()
    
    # active와 open_ended 상태의 공고를 조회
    cursor.execute("""
        SELECT id, external_id, posted_date, due_time, status 
        FROM jobs 
        WHERE status IN ('active', 'open_ended')
    """)
    jobs = cursor.fetchall()
    
    for row in jobs:
        job_id, external_id, posted_date, due_time, current_status = row
        job = {
            "id": job_id,
            "external_id": external_id,
            "posted_date": posted_date,
            "due_time": due_time,
            "status": current_status
        }
        
        # due_time 기준 유효성 검사
        if due_time is not None:
            if datetime.now() > due_time:
                new_status = "closed"
            else:
                new_status = "active"
        else:
            # due_time이 없는 경우(None), posted_date 기준 (30 일 이상 경과 시 closed)
            if posted_date is not None and (datetime.now() - posted_date > timedelta(days=30)):
                new_status = "closed"
            else:
                new_status = "open_ended"
        
        # (옵션) API 검증 로직 적용 가능:
        # new_status = validate_job_via_api(job)
        
        if new_status != current_status:
            cursor.execute("""
                UPDATE jobs 
                SET status = %s, last_validated_at = NOW() 
                WHERE id = %s
            """, (new_status, job_id))
            print(f"Job {external_id} 상태 변경: {current_status} -> {new_status}")
    
    conn.commit()
    cursor.close()
    conn.close()
    
    return "Refresh complete"

if __name__ == "__main__":
    refresh_jobs()
