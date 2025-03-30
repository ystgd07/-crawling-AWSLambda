import requests
import psycopg2
import os
import datetime
from elasticsearch import Elasticsearch

def infer_job_category(title):
    """직무명에서 카테고리 추론"""
    title_lower = title.lower()
    
    # 직무 분류 사전 - 실제 구현 시 더 확장
    categories = {
        "frontend": ["프론트엔드", "프론트", "front", "react", "vue", "angular", "웹 개발", "퍼블리셔"],
        "backend": ["백엔드", "back", "서버", "server", "java", "spring", "django", "node", "php"],
        "mobile": ["모바일", "안드로이드", "ios", "앱", "flutter", "react native", "swift"],
        "devops": ["데브옵스", "devops", "인프라", "aws", "kubernetes", "docker", "ci/cd", "클라우드"],
        "data": ["데이터", "data", "머신러닝", "ml", "ai", "빅데이터", "분석", "scientist", "engineer"],
        "security": ["보안", "security", "침투", "해킹", "취약점", "암호화"],
        "fullstack": ["풀스택", "full stack", "full-stack"]
    }
    
    # 각 카테고리별 매칭 점수 계산
    scores = {}
    for category, keywords in categories.items():
        scores[category] = sum(1 for keyword in keywords if keyword in title_lower)
    
    # 가장 높은 점수의 카테고리 반환
    if max(scores.values(), default=0) > 0:
        return max(scores.items(), key=lambda x: x[1])[0]
    
    return "other"  # 분류 불가능한 경우

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
    
    # Elasticsearch 연결 설정
    try:
        es = Elasticsearch(
            [os.environ["ES_HOST"]],
            basic_auth=(
                os.environ["ES_USERNAME"], 
                os.environ["ES_PASSWORD"]
            ),
            verify_certs=False  # 개발 환경에서만 사용
        )
        es_available = True
    except Exception as e:
        print(f"Elasticsearch 연결 실패: {e}")
        es_available = False

    for job in jobs:
        job_id = job["id"]
        title = job["position"]
        company = job["company"]["name"]
        location = job.get("address",{}).get("location")
        annual_from = job.get("annual_from")
        annual_to = job.get("annual_to")
        position=job.get("position")
        source = "wanted"
        detailurl = f"https://www.wanted.co.kr/wd/{job_id}"
        
        # 직무 카테고리 유추
        job_category = infer_job_category(title)

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

        # INSERT: 중복 방지를 위해 (external_id, source) UNIQUE 제약을 사용
        cursor.execute("""
            INSERT INTO jobs (external_id, title, company, location, position, annual_from, annual_to, source, detailurl, due_time)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (external_id, source) DO NOTHING
        """, (job_id, title, company, location, position, annual_from, annual_to, source, detailurl, due_time_dt))
        
        # Elasticsearch에 색인
        if es_available:
            try:
                # Elasticsearch 문서 생성
                es_doc = {
                    "external_id": job_id,
                    "title": title,
                    "company": company,
                    "location": location, 
                    "position": position,
                    "annual_from": annual_from,
                    "annual_to": annual_to,
                    "source": source,
                    "detailurl": detailurl,
                    "due_time": due_time_str,
                    "job_category": job_category,
                    "created_at": datetime.datetime.now().isoformat()
                }
                
                # 문서 색인 (ID는 source_external_id 형식으로 지정)
                es.index(index="jobs", id=f"{source}_{job_id}", document=es_doc)
                print(f"Elasticsearch에 문서 색인: {source}_{job_id}")
            except Exception as e:
                print(f"Elasticsearch 색인 실패 ({job_id}): {e}")

    conn.commit()
    cursor.close()
    conn.close()

    return {"statusCode": 200, "body": f"{len(jobs)} jobs inserted."}
