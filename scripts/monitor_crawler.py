import os
import json
import time
import random
import datetime
import pytz
import re
import requests
import pandas as pd
from bs4 import BeautifulSoup

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

# 포맷 정의
YESTERDAY_FULL = YESTERDAY.strftime('%Y-%m-%d') # 2026-02-01
YESTERDAY_DOT = YESTERDAY.strftime('%y.%m.%d')   # 25.02.01

print(f"📅 타겟 날짜: {YESTERDAY_FULL}")

# --- [1. 브라우저 설정] ---
def get_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--lang=ko_KR")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

# --- [2. 크롤러: 뽐뿌 (누락 방지 강화)] ---
def get_ppomppu_posts(driver):
    print("running ppomppu crawler...")
    posts = []
    base_url = "https://www.ppomppu.co.kr/zboard/zboard.php?id=phone&page={}"
    
    # 뽐뿌는 중간에 공지 등으로 날짜가 섞일 수 있어, 
    # 날짜가 지났다고 바로 끊지 않고 10페이지까지는 무조건 훑습니다.
    for page in range(1, 11): 
        try:
            print(f"  - Ppomppu page {page} scanning...")
            driver.get(base_url.format(page))
            time.sleep(random.uniform(1.0, 2.0))
            
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            rows = soup.select('tr.baseList')
            
            if not rows: break
            
            page_match_count = 0
            
            for row in rows:
                time_span = row.select_one('.baseList-time')
                if not time_span: continue
                date_td = time_span.find_parent('td')
                if not date_td or not date_td.get('title'): continue
                
                raw_date = date_td['title'].split(' ')[0] # 26.02.01
                post_date = "20" + raw_date.replace('.', '-') # 2026-02-01
                
                # 정확히 어제 날짜인 것만 수집
                if post_date == YESTERDAY_FULL:
                    title_tag = row.select_one('.baseList-title')
                    if not title_tag: continue
                    
                    title = title_tag.text.strip()
                    link = "https://www.ppomppu.co.kr/zboard/" + title_tag['href']
                    views = int(row.select_one('.baseList-views').text.strip() or 0)
                    comments = int(row.select_one('.baseList-c').text.strip() or 0)
                    
                    posts.append({'source': 'ppomppu', 'title': title, 'link': link, 'views': views, 'comments': comments})
                    page_match_count += 1
            
            # 한 페이지를 다 털었는데 어제 데이터가 하나도 없고, 
            # 페이지도 5페이지가 넘어가면 그때 그만둠 (안전장치)
            if page_match_count == 0 and page > 5:
                # 혹시 모르니 마지막으로 체크: 현재 페이지의 날짜들이 전부 과거인가?
                # (이 로직은 복잡하니 일단 10페이지 강제 스캔으로 유지)
                pass

        except Exception as e:
            print(f"Err Ppomppu p{page}: {e}")
            
    return posts

# --- [3. 크롤러: 디시 (기존 유지)] ---
def get_dc_posts(driver):
    print("running dc crawler...")
    posts = []
    base_url = "https://gall.dcinside.com/mgallery/board/lists/?id=mvnogallery&page={}"
    page = 1
    
    while True:
        try:
            print(f"  - DC page {page} scanning...")
            driver.get(base_url.format(page))
            time.sleep(random.uniform(1.0, 2.0))
            
            if "디시인사이드입니다" in driver.title and "알뜰폰" not in driver.title:
                print("  - Blocked by DC.")
                break

            soup = BeautifulSoup(driver.page_source, 'html.parser')
            rows = soup.select('tr.ub-content.us-post')
            
            if not rows: break
                
            stop_crawling = False
            
            for row in rows:
                if row.get('data-type') == 'icon_notice': continue
                date_tag = row.select_one('.gall_date')
                if not date_tag or not date_tag.get('title'): continue
                
                post_date = date_tag['title'].split(' ')[0] # 2026-02-01
                
                if post_date == YESTERDAY_FULL:
                    title_tag = row.select_one('.gall_tit > a')
                    if not title_tag: continue
                    title = title_tag.text.strip()
                    link = "https://gall.dcinside.com" + title_tag['href']
                    views_tag = row.select_one('.gall_count')
                    views = int(views_tag.text.strip().replace(',', '')) if views_tag and views_tag.text.strip().isdigit() else 0
                    reply_tag = row.select_one('.reply_num')
                    comments = int(reply_tag.text.strip('[]')) if reply_tag else 0
                    
                    posts.append({'source': 'dc', 'title': title, 'link': link, 'views': views, 'comments': comments})
                
                elif post_date < YESTERDAY_FULL:
                    stop_crawling = True
            
            if stop_crawling:
                break
            
            page += 1
            if page > 50: break 
            
        except Exception as e:
            print(f"Err DC p{page}: {e}")
            break
            
    return posts

# --- [4. 분석, 저장, 알림] ---
def analyze_and_notify(p_posts, d_posts):
    total_posts = p_posts + d_posts
    
    df = pd.DataFrame(total_posts)
    p_cnt = len(p_posts)
    d_cnt = len(d_posts)
    
    # [수정] 커뮤니티 활성도 기준 (하드코딩 - 추후 평균으로 변경 추천)
    p_status = "🔴 과열" if p_cnt >= 180 else ("🟢 평온" if p_cnt < 80 else "🟡 활발")
    d_status = "🔴 과열" if d_cnt >= 600 else ("🟢 평온" if d_cnt < 300 else "🟡 활발")

    # 브랜드 키워드
    brands = {
        '세븐모바일': ['세븐모바일', '7모', 'sk7', 'sk텔링크'],
        '모빙': ['모빙'],
        '리브엠': ['리브엠', '리브모바일', 'kb'],
        '이야기': ['이야기', '이야기모바일'],
        '헬로모바일': ['헬로모바일', '헬모'],
        '프리티': ['프리티'],
        '티플러스': ['티플러스', '티플'],
        '티다이렉트': ['티다이렉트', '티다', 't다이렉트', 't다'],
        'KT엠모바일': ['kt엠모바일', '엠모바일', '엠모', 'ktm'],
        '스카이라이프': ['스카이라이프', '스카라', 'skylife'],
        '유모바일': ['유모바일', '유모', 'u모바일', '유알모'],
        'SKT_Air': ['skt에어', 'skt air']
    }
    
    brand_counts = {}
    sov_lines = []
    
    # [수정] 세븐모바일 링크 수집용 리스트
    seven_mobile_links = []

    if not df.empty:
        for b_name, keywords in brands.items():
            # 해당 브랜드가 포함된 행만 필터링
            filtered_df = df[df['title'].apply(lambda x: any(k in x.lower() for k in keywords))]
            cnt = len(filtered_df)
            brand_counts[b_name] = int(cnt)
            
            # 0건이면 리스트에 추가 X (숨김 처리)
            if cnt > 0:
                sov_lines.append(f"• {b_name}: {cnt}건")
                
                # [수정] 세븐모바일이면 링크 수집
                if b_name == '세븐모바일':
                    for _, row in filtered_df.iterrows():
                        seven_mobile_links.append(f"  └ <{row['link']}|{row['title']}>")
    else:
        for b_name in brands: brand_counts[b_name] = 0
        sov_lines = ["데이터 없음"]

    sov_msg = "\n".join(sov_lines)
    
    # 세븐모바일 링크가 있다면 메시지에 추가
    if seven_mobile_links:
        sov_msg += "\n\n*📌 세븐모바일 언급 게시글:*\n" + "\n".join(seven_mobile_links)

    # Top 5 포맷팅
    def format_list(sub_df):
        if sub_df.empty: return "없음"
        top5 = sub_df.sort_values(by='views', ascending=False).head(5)
        lines = []
        for idx, row in top5.iterrows():
            title = row['title']
            icon = ""
            if any(k in title for k in ['0원', '무제한', '평생', '대란', '공짜']): icon = " 💰"
            lines.append(f"• <{row['link']}|{title}>{icon} (👁️ {row['views']:,} / 💬 {row['comments']})")
        
        top5_data = top5[['title', 'link', 'views', 'comments']].to_dict('records')
        return "\n".join(lines), top5_data

    p_msg, p_top5 = format_list(pd.DataFrame(p_posts))
    d_msg, d_top5 = format_list(pd.DataFrame(d_posts))

    # --- [대시보드 데이터 저장] ---
    history_file = 'data/dashboard_history.json'
    history_data = []
    
    if os.path.exists(history_file):
        with open(history_file, 'r', encoding='utf-8') as f:
            try: history_data = json.load(f)
            except: pass
    
    today_entry = {
        "date": YESTERDAY_FULL,
        "total_volume": { "ppomppu": p_cnt, "dc": d_cnt },
        "brand_sov": brand_counts,
        "top_posts": { "ppomppu": p_top5, "dc": d_top5 }
    }
    
    history_data = [d for d in history_data if d['date'] != YESTERDAY_FULL]
    history_data.append(today_entry)
    history_data.sort(key=lambda x: x['date'])
    
    os.makedirs('data', exist_ok=True)
    with open(history_file, 'w', encoding='utf-8') as f:
        json.dump(history_data, f, ensure_ascii=False, indent=4)

    # --- [슬랙 전송: 제목 및 섹션명 수정됨] ---
    slack_text = f"""
*[📊 {YESTERDAY_FULL} 알뜰폰 커뮤니티 모니터링]*

*🌡️ 커뮤니티 활성도*
• 뽐뿌: {p_status} ({p_cnt}개)
• 디시: {d_status} ({d_cnt}개)

*📈 브랜드 언급량*
{sov_msg}

*1️⃣ 뽐뿌 휴대폰포럼 (Top 5)*
{p_msg}

*2️⃣ 디시 알뜰폰 갤러리 (Top 5)*
{d_msg}

👉 <https://rodolfochoi911-lgtm.github.io/competitor-monitor/|웹 대시보드 확인하기>
    """
    
    if SLACK_WEBHOOK_URL:
        requests.post(SLACK_WEBHOOK_URL, json={"text": slack_text})

    # 백업 저장
    os.makedirs('data/monitoring', exist_ok=True)
    with open(f'data/monitoring/data_{YESTERDAY_FULL}.json', 'w', encoding='utf-8') as f:
        json.dump(total_posts, f, ensure_ascii=False, indent=4)

if __name__ == "__main__":
    driver = get_driver()
    try:
        p_data = get_ppomppu_posts(driver)
        d_data = get_dc_posts(driver)
        analyze_and_notify(p_data, d_data)
        print("✅ 작업 완료")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        driver.quit()
