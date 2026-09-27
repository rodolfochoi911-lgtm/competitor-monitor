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
from collections import Counter

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# --- [설정] ---
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

TZ_KST = pytz.timezone('Asia/Seoul')
NOW = datetime.datetime.now(TZ_KST)
YESTERDAY = NOW - datetime.timedelta(days=1)

YESTERDAY_FULL = YESTERDAY.strftime('%Y-%m-%d') # 2026-02-01
YESTERDAY_DOT = YESTERDAY.strftime('%y.%m.%d')   # 25.02.01

print(f"📅 타겟 날짜: {YESTERDAY_FULL}")

BRAND_KEYWORDS = {
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

# --- [2. 크롤러: 뽐뿌 (하이브리드 정밀 수집)] ---
def get_ppomppu_posts(driver):
    print("running ppomppu crawler...")
    posts = []
    base_url = "https://www.ppomppu.co.kr/zboard/zboard.php?id=phone&page={}"
    
    for page in range(1, 21): 
        try:
            driver.get(base_url.format(page))
            time.sleep(random.uniform(1.0, 2.0))
            
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            rows = soup.find_all('tr') # 모든 행 탐색
            
            valid_cnt_in_page = 0
            
            for row in rows:
                title_elem = row.select_one('font.list_title') or row.select_one('a')
                if not title_elem: continue
                
                # [수정] 날짜 추출 우선순위 강화 (정확도 향상)
                post_date = ""
                
                # 1. .baseList-time 클래스가 있다면 가장 정확 (부모 td의 title 속성)
                time_span = row.select_one('.baseList-time')
                if time_span:
                    date_td = time_span.find_parent('td')
                    if date_td and date_td.get('title'):
                        raw_date = date_td['title'].split(' ')[0]
                        post_date = "20" + raw_date.replace('.', '-')
                
                # 2. 없다면 title 속성 직접 검색
                if not post_date:
                    date_td = row.find('td', title=re.compile(r'\d{2}\.\d{2}\.\d{2}'))
                    if date_td:
                        raw_date = date_td['title'].split(' ')[0]
                        post_date = "20" + raw_date.replace('.', '-')
                
                # 3. 그래도 없다면 텍스트 정규식 (최후의 수단)
                if not post_date:
                    date_match = re.search(r'\d{2}\.\d{2}\.\d{2}', row.text)
                    if date_match:
                        post_date = "20" + date_match.group().replace('.', '-')

                # 날짜 일치 확인
                if post_date == YESTERDAY_FULL:
                    link_elem = row.select_one('a[href*="view.php"]')
                    if not link_elem: continue
                    
                    title = title_elem.text.strip()
                    link = "https://www.ppomppu.co.kr/zboard/" + link_elem['href']
                    
                    # [수정] 조회수/댓글수 우선순위 강화 (숫자 꼬임 방지)
                    views, comments = 0, 0
                    
                    # 1. 클래스로 찾기 (가장 정확)
                    view_tag = row.select_one('.baseList-views')
                    cmt_tag = row.select_one('.baseList-c') or row.select_one('.list_comment2')
                    
                    if view_tag:
                        views = int(view_tag.text.strip().replace(',', '') or 0)
                    else:
                        # 2. 텍스트에서 찾기 (리스크 있음)
                        views_match = re.findall(r'\d{1,3}(?:,\d{3})*', row.text)
                        if views_match: views = int(views_match[-1].replace(',', ''))
                    
                    if cmt_tag:
                        comments = int(cmt_tag.text.strip().replace(',', '') or 0)

                    posts.append({'source': 'ppomppu', 'title': title, 'link': link, 'views': views, 'comments': comments})
                    valid_cnt_in_page += 1
            
            if valid_cnt_in_page == 0 and page > 10:
                print("  - No more posts found. Stopping.")
                break

        except Exception as e:
            print(f"Err Ppomppu p{page}: {e}")
            
    return posts

# --- [3. 크롤러: 디시 (기존 유지)] ---
def get_dc_posts(driver):
    print("running dc crawler...")
    posts = []
    base_url = "https://gall.dcinside.com/mgallery/board/lists/?id=mvnogallery&page={}"
    
    for page in range(1, 51):
        try:
            driver.get(base_url.format(page))
            time.sleep(random.uniform(1.0, 2.0))
            
            if "디시인사이드입니다" in driver.title and "알뜰폰" not in driver.title:
                break

            soup = BeautifulSoup(driver.page_source, 'html.parser')
            rows = soup.select('tr.ub-content.us-post')
            
            if not rows: break
                
            stop_crawling = False
            
            for row in rows:
                if row.get('data-type') == 'icon_notice': continue
                date_tag = row.select_one('.gall_date')
                if not date_tag or not date_tag.get('title'): continue
                
                post_date = date_tag['title'].split(' ')[0]
                
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
            
            if stop_crawling: break
            
        except Exception as e:
            print(f"Err DC p{page}: {e}")
            break
            
    return posts

# --- [4. 분석 로직] ---
def extract_keyword_counts(df):
    if df.empty: return Counter()
    all_titles = " ".join(df['title'].tolist())
    all_titles = re.sub(r'[^\w\s]', ' ', all_titles)
    words = all_titles.split()
    
    # [수정] 불용어 추가 (있음, 알뜰 등)
    stopwords = set([
        '질문', '후기', '정보', '요금제', '알뜰폰', '추천', '있나요', '나요', '가요', '건가요',
        '오늘', '내일', '이번달', '2월', '1월', '근데', '진짜', '혹시', '아니', '너무',
        '유심', '번호이동', '기변', '신규', '개통', '모바일', '사람', '생각', '지금', '어제',
        '약정', '결합', '할인', '카드', '데이터', '무제한', '평생', '개월', '년',
        'skt', 'kt', 'lg', 'lgu', 'sk', 'kt망', 'lgu+', 'u+', 'sk망', '헬로',
        'vs', '이거', '저거', '그거', '뭐야', '시발', '존나', 'ㅋㅋ', 'ㅎㅎ', 'ㅠㅠ',
        '문의', '질문좀', '대해', '관련', '어떤가요', '무슨', '어디', '어떻게',
        '선택', '위약금', '조건', '정책', '비교', '변경', '이동', '사용', '가입', '해지',
        '있음', '알뜰', '요금', '번호', '이동', '통신사' # 추가된 노이즈
    ])
    filtered_words = [w for w in words if len(w) >= 2 and w.lower() not in stopwords]
    return Counter(filtered_words)


def extract_top_keywords(df):
    return extract_keyword_counts(df).most_common(10)


def count_brand_mentions(df):
    """Return the existing brand metric for one dataframe slice."""
    counts = {}
    for brand, keywords in BRAND_KEYWORDS.items():
        if df.empty:
            counts[brand] = 0
            continue
        matched = df['title'].apply(
            lambda title: any(keyword in str(title).lower() for keyword in keywords)
        )
        counts[brand] = int(matched.sum())
    return counts


def aggregate_metrics_by_source(df):
    """Build additive source-level metrics without changing legacy totals."""
    brand_sov_by_source = {}
    keywords_raw_by_source = {}

    for source in ('ppomppu', 'dc'):
        source_df = df[df['source'] == source]
        brand_sov_by_source[source] = count_brand_mentions(source_df)
        keywords_raw_by_source[source] = dict(extract_keyword_counts(source_df))

    return brand_sov_by_source, keywords_raw_by_source

def analyze_and_notify(p_posts, d_posts):
    total_posts = p_posts + d_posts
    if not total_posts:
        print("⚠️ 수집된 데이터 0건")
        return

    df = pd.DataFrame(total_posts)
    p_cnt = len(p_posts)
    d_cnt = len(d_posts)
    
    p_status = "🔴 과열" if p_cnt >= 180 else ("🟢 평온" if p_cnt < 80 else "🟡 활발")
    d_status = "🔴 과열" if d_cnt >= 600 else ("🟢 평온" if d_cnt < 300 else "🟡 활발")

    # 1. 브랜드 점유율 (세븐모바일 고정 노출 로직 추가)
    brand_counts = count_brand_mentions(df)
    seven_links = [] # 세븐모바일 링크 수집

    seven_keywords = BRAND_KEYWORDS['세븐모바일']
    seven_filtered = df[df['title'].apply(
        lambda title: any(keyword in str(title).lower() for keyword in seven_keywords)
    )]
    for _, row in seven_filtered.iterrows():
        seven_links.append(f"  └ <{row['link']}|{row['title']}>")

    brand_sov_by_source, keywords_raw_by_source = aggregate_metrics_by_source(df)

    # [수정] 출력 순서 제어 (세븐모바일 1순위, 나머지 >0 건만)
    sov_lines = []
    
    # 1. 세븐모바일 무조건 출력
    seven_cnt = brand_counts.get('세븐모바일', 0)
    sov_lines.append(f"• 세븐모바일: {seven_cnt}건")
    
    # 2. 나머지 브랜드 (0건이면 제외)
    for b_name, cnt in brand_counts.items():
        if b_name == '세븐모바일': continue
        if cnt > 0:
            sov_lines.append(f"• {b_name}: {cnt}건")

    sov_msg = "\n".join(sov_lines)

    # 2. 핫 키워드 (Top 10)
    top_keywords = extract_top_keywords(df)
    keyword_msg = ""
    for word, count in top_keywords:
        keyword_msg += f"• {word}: {count}건\n"
    if not keyword_msg: keyword_msg = "• 특이사항 없음"

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

    # 데이터 저장
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
        "top_keywords": dict(top_keywords),
        "top_posts": { "ppomppu": p_top5, "dc": d_top5 },
        "brand_sov_by_source": brand_sov_by_source,
        "keywords_raw_by_source": keywords_raw_by_source
    }
    
    history_data = [d for d in history_data if d['date'] != YESTERDAY_FULL]
    history_data.append(today_entry)
    history_data.sort(key=lambda x: x['date'])
    
    os.makedirs('data', exist_ok=True)
    with open(history_file, 'w', encoding='utf-8') as f:
        json.dump(history_data, f, ensure_ascii=False, indent=4)

    # 슬랙 전송
    seven_block = ""
    if seven_links:
        seven_block = f"\n*📌 세븐모바일 언급 ({len(seven_links)}건)*\n" + "\n".join(seven_links)

    slack_text = f"""
*[📊 {YESTERDAY_FULL} 알뜰폰 커뮤니티 모니터링]*

*🌡️ 커뮤니티 활성도*
• 뽐뿌: {p_status} ({p_cnt}개)
• 디시: {d_status} ({d_cnt}개)

*📈 브랜드 언급량 (SOV)*
{sov_msg}{seven_block}

*🔥 핫 키워드 (Top 10)*
{keyword_msg}

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
