import requests
import psycopg2
import os
import datetime
from elasticsearch import Elasticsearch
import time

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
        best_category = max(scores.items(), key=lambda x: x[1])[0]
        best_score = scores[best_category]
        # 카테고리, 최고 점수, 모든 점수를 반환
        return best_category, best_score, scores
    
    return "other", 0, {}

def lambda_handler(event, context):
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
            [os.environ.get("ES_HOST")],
            basic_auth=(
                os.environ.get("ES_USERNAME"), 
                os.environ.get("ES_PASSWORD")
            ),
            verify_certs=False  # 개발 환경에서만 사용
        )
        es_available = True

    except Exception as e:
        print(f"Elasticsearch 연결 실패: {e}")
        es_available = False

    total_processed = 0
    
    # 다양한 검색 필터 설정
    job_filters = [
        {},  # 기본(필터 없음)
        {"job_sort": "job.latest_order"},  # 최신순
        {"job_sort": "job.popularity_order"},  # 인기순
        {"tag_type_id": "669"},  # 개발 직군
        {"tag_type_id": "677"},  # 데이터 직군
        {"tag_type_id": "674"},  # 디자인 직군
        {"tag_type_id": "678"},  # 기획 직군
    ]
    
    # 지역 필터
    locations = ["서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종"]
    
    # 페이지네이션 설정
    pages_per_filter = 2  # 필터당 페이지 수
    limit = 100  # 페이지당 데이터 수
    
    # 각 필터와 지역별로 데이터 수집
    for job_filter in job_filters:
        for location in locations:
            current_filter = job_filter.copy()
            if location:
                current_filter["location"] = location
                
            for page in range(pages_per_filter):
                offset = page * limit
                url = "https://www.wanted.co.kr/api/v4/jobs"
                params = {
                    "limit": limit,
                    "offset": offset,
                    "country": "kr",
                    **current_filter
                }
                headers = {"User-Agent": "Mozilla/5.0"}

                # API 요청
                try:
                    filter_desc = ", ".join([f"{k}={v}" for k, v in current_filter.items()])
                    print(f"필터 [{filter_desc}] 페이지 {page+1}/{pages_per_filter} 요청 중... (offset: {offset})")
                    res = requests.get(url, params=params, headers=headers, timeout=10)
                    jobs = res.json().get("data", [])
                    
                    if not jobs:
                        print(f"더 이상 데이터가 없습니다. 필터 [{filter_desc}] 페이지 {page+1}에서 종료합니다.")
                        break
                        
                    print(f"필터 [{filter_desc}] 페이지 {page+1}에서 {len(jobs)}개 데이터를 가져왔습니다.")
                except Exception as e:
                    print(f"API 요청 실패: {e}")
                    continue

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
                    job_category, category_score, all_scores = infer_job_category(title)

                    # due_time 추출: 문자열이 제공되면 datetime으로 파싱, 없으면 None
                    due_time_str = job.get("due_time")
                    if due_time_str:
                        try:
                            # due_time이 ISO 8601 형식이라고 가정 (예: "2025-04-08")
                            due_time_dt = datetime.datetime.fromisoformat(due_time_str)
                        except Exception as e:
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
                                "category_score": category_score,           # 최고 점수
                                "category_scores": all_scores,              # 모든 카테고리 점수
                                "created_at": datetime.datetime.now().isoformat(),
                                "filter_used": current_filter               # 사용된 필터 정보
                            }
                            
                            # 문서 색인 (ID는 source_external_id 형식으로 지정)
                            es.index(index="jobs", id=f"{source}_{job_id}", document=es_doc)
                        except Exception as e:
                            print(f"Elasticsearch 색인 실패 ({job_id}): {e}")
                    
                    total_processed += 1
                
                # 페이지당 일시 중지 (API 속도 제한 방지)
                if page < pages_per_filter - 1:
                    time.sleep(1)
    
    conn.commit()
    cursor.close()
    conn.close()

    return {"statusCode": 200, "body": f"{total_processed} jobs inserted."}
