import os
import json
import time
import random
import datetime
import pytz
import requests
import pandas as pd
from bs4 import BeautifulSoup

# --- [Selenium 관련 라이브러리] ---
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# --- [설정] ---
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

# 한국 시간 기준 '어제' 날짜 계산
TZ_KST = pytz.timezone('Asia/Seoul')
NOW = datetime.datetime.now(TZ_KST)
YESTERDAY = NOW - datetime.timedelta(days=1)

# 포맷 1: 2025-02-01 (저장 및 로직용)
YESTERDAY_FULL = YESTERDAY.strftime('%Y-%m-%d')
# 포맷 2: 02-01 (디시 등 비교용)
YESTERDAY_SHORT = YESTERDAY.strftime('%m-%d')
# 포맷 3: 25.02.01 (뽐뿌 비교용 - 연도 2자리)
YESTERDAY_DOT = YESTERDAY.strftime('%y.%m.%d')

print(f"📅 타겟 날짜: {YESTERDAY_FULL} (어제 데이터 수집)")

# --- [1. 브라우저 설정 (Anti-Bot)] ---
def get_driver():
    chrome_options = Options()
    
    # 헤드리스 모드 (서버용)
    chrome_options.add_argument("--headless") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    
    # 봇 탐지 회피 (User-Agent 변조)
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    
    # 드라이버 자동 설치 및 실행
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    
    return driver

# --- [2. 크롤링 함수] ---

def get_ppomppu_posts(driver):
    """뽐뿌 휴대폰포럼 크롤링 (Selenium)"""
    print("running ppomppu crawler...")
    posts = []
    base_url = "https://www.ppomppu.co.kr/zboard/zboard.php?id=phone&page={}"
    
    for page in range(1, 11): # 1~10페이지 탐색
        try:
            driver.get(base_url.format(page))
            time.sleep(random.uniform(2, 4)) # 휴먼 터치 (2~4초 대기)
            
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            rows = soup.select('tr.common-list0, tr.common-list1')
            
            if not rows: break
            
            stop_flag = False
            for row in rows:
                date_tag = row.select_one('.board_date')
                if not date_tag: continue
                date_text = date_tag.text.strip() 
                
                # 오늘 글(시간 표시)은 스킵
                if ":" in date_text: continue
                
                # 날짜 비교 (뽐뿌는 YY.MM.DD)
                if date_text == YESTERDAY_DOT:
                    title_elem = row.select_one('font.list_title') or row.select_one('a')
                    title = title_elem.text.strip()
                    link = "https://www.ppomppu.co.kr/zboard/" + row.select_one('a')['href']
                    
                    views = int(row.select_one('.board_hit').text.strip().replace(',', ''))
                    
                    # 댓글 수 파싱
                    comment_span = row.select_one('.list_comment2')
                    comments = int(comment_span.text.strip()) if comment_span else 0
                    
                    posts.append({
                        'source': 'ppomppu', 'title': title, 'link': link,
                        'views': views, 'comments': comments
                    })
                elif date_text < YESTERDAY_DOT:
                    stop_flag = True
            
            if stop_flag and page > 3: 
                break
                
        except Exception as e:
            print(f"Error on Ppomppu page {page}: {e}")
            
    return posts

def get_dc_posts(driver):
    """디시 알뜰폰 갤러리 크롤링 (Selenium)"""
    print("running dc crawler...")
    posts = []
    base_url = "https://gall.dcinside.com/mgallery/board/lists/?id=mvno&page={}"
    
    target_date_dc = YESTERDAY.strftime('%m.%d') 
    
    for page in range(1, 15):
        try:
            driver.get(base_url.format(page))
            time.sleep(random.uniform(2, 4))
            
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            rows = soup.select('tr.ub-content')
            
            if not rows: break
            
            for row in rows:
                if 'ub-notice' in row.get('class', []): continue 
                
                date_tag = row.select_one('.gall_date')
                if not date_tag: continue
                date_text = date_tag.text.strip() 
                
                if ":" in date_text: continue
                
                if date_text == target_date_dc:
                    title = row.select_one('.gall_tit > a').text.strip()
                    link = "https://gall.dcinside.com" + row.select_one('.gall_tit > a')['href']
                    views = int(row.select_one('.gall_count').text.strip().replace(',', '') or 0)
                    
                    reply_tag = row.select_one('.reply_num')
                    comments = int(reply_tag.text.strip('[]')) if reply_tag else 0
                    
                    posts.append({
                        'source': 'dc', 'title': title, 'link': link,
                        'views': views, 'comments': comments
                    })
            
            if page > 10 and len(posts) == 0: 
                break
                
        except Exception as e:
            print(f"Error on DC page {page}: {e}")
            
    return posts

# --- [3. 분석 및 알림] ---

def analyze_and_notify(p_posts, d_posts):
    total_posts = p_posts + d_posts
    df = pd.DataFrame(total_posts)
    
    # [수정] 데이터가 없어도 에러 안 나게 안전장치 추가
    if df.empty:
        print("⚠️ 수집된 데이터가 0건입니다.")
        if SLACK_WEBHOOK_URL:
            requests.post(SLACK_WEBHOOK_URL, json={"text": f"⚠️ [{YESTERDAY_FULL}] 수집된 게시글이 0건입니다. (사이트 차단 여부 확인 필요)"})
        return

    # 1. 시장 지진계
    p_cnt = len(p_posts)
    d_cnt = len(d_posts)
    p_status = "🔴 과열" if p_cnt >= 100 else ("🟢 평온" if p_cnt < 50 else "🟡 활발")
    d_status = "🔴 과열" if d_cnt >= 80 else ("🟢 평온" if d_cnt < 30 else "🟡 활발")

    # 2. 브랜드 점유율
    brands = {
        '세븐모바일': ['세븐모바일', '7모', 'sk7', 'sk텔링크'],
        '모빙': ['모빙'],
        '리브엠': ['리브엠', '리브모바일', 'kb'],
        '이야기': ['이야기', '이야기모바일']
    }
    
    sov_lines = []
    for b_name, keywords in brands.items():
        cnt = df[df['title'].apply(lambda x: any(k in x for k in keywords))].shape[0]
        sov_lines.append(f"• {b_name}: {cnt}건")
    sov_msg = "\n".join(sov_lines)

    # 3. Top 5 포맷팅 (안전장치 포함)
    def format_list(sub_df):
        if sub_df.empty: return "없음"
        
        # 조회수 내림차순 정렬
        if 'views' not in sub_df.columns: return "데이터 없음"
            
        top5 = sub_df.sort_values(by='views', ascending=False).head(5)
        lines = []
        for idx, row in top5.iterrows():
            title = row['title']
            icon = ""
            if any(k in title for k in ['0원', '무제한', '평생', '대란', '공짜']):
                icon = " 💰"
            lines.append(f"{idx+1}. <{row['link']}|{title}>{icon} (👁️ {row['views']:,} / 💬 {row['comments']})")
        return "\n".join(lines)

    # 메시지 작성
    slack_text = f"""
*[📊 {YESTERDAY_FULL} 알뜰폰 시장 모니터링]*

*🌡️ 시장 활성도 (어제 게시글 수)*
• 뽐뿌: {p_status} ({p_cnt}개)
• 디시: {d_status} ({d_cnt}개)

*📈 브랜드 언급량 (SOV)*
{sov_msg}

*1️⃣ 뽐뿌 휴대폰포럼 (Top 5)*
{format_list(pd.DataFrame(p_posts))}

*2️⃣ 디시 알뜰폰 갤러리 (Top 5)*
{format_list(pd.DataFrame(d_posts))}

👉 <https://github.com/YOUR_ID/YOUR_REPO|웹 대시보드 바로가기>
    """
    
    if SLACK_WEBHOOK_URL:
        requests.post(SLACK_WEBHOOK_URL, json={"text": slack_text})
        print("Slack sent.")
    else:
        print(slack_text)

    # 데이터 저장
    os.makedirs('data/monitoring', exist_ok=True)
    with open(f'data/monitoring/data_{YESTERDAY_FULL}.json', 'w', encoding='utf-8') as f:
        json.dump(total_posts, f, ensure_ascii=False, indent=4)

# --- [메인 실행] ---
if __name__ == "__main__":
    driver = get_driver()
    try:
        p_data = get_ppomppu_posts(driver)
        d_data = get_dc_posts(driver)
        analyze_and_notify(p_data, d_data)
    except Exception as e:
        print(f"Critical Error: {e}")
    finally:
        driver.quit()
