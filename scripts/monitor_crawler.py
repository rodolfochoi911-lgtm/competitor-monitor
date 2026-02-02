import os
import json
import time
import datetime
import pytz
import pandas as pd
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# --- [설정] ---
TZ_KST = pytz.timezone('Asia/Seoul')
NOW = datetime.datetime.now(TZ_KST)
YESTERDAY = NOW - datetime.timedelta(days=1)

# 날짜 포맷 정의
YESTERDAY_FULL = YESTERDAY.strftime('%Y-%m-%d')
YESTERDAY_DOT = YESTERDAY.strftime('%y.%m.%d') # 뽐뿌용 (25.02.01)
YESTERDAY_SHORT = YESTERDAY.strftime('%m.%d')  # 디시용 (02.01)

print(f"🔍 [진단 시작] 타겟 날짜: {YESTERDAY_FULL}")
print(f"👉 뽐뿌 타겟: {YESTERDAY_DOT} / 디시 타겟: {YESTERDAY_SHORT}")

# --- [브라우저 설정] ---
def get_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    # 일반적인 윈도우 크롬처럼 위장
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

# --- [크롤러: 뽐뿌] ---
def get_ppomppu_posts(driver):
    print("\n--- [뽐뿌 크롤링 진단] ---")
    posts = []
    url = "https://www.ppomppu.co.kr/zboard/zboard.php?id=phone&page=1"
    
    driver.get(url)
    time.sleep(3) # 로딩 대기
    
    # 1. 접속 확인
    print(f"DEBUG: 페이지 제목 = {driver.title}")
    
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    rows = soup.select('tr.common-list0, tr.common-list1')
    
    print(f"DEBUG: 발견된 게시글 행(Row) 수 = {len(rows)}")
    
    if len(rows) == 0:
        print("❌ 게시글을 하나도 못 찾았습니다. (차단되었거나 선택자 변경됨)")
        # HTML 일부 출력해서 확인 (차단 메시지 있는지)
        print(f"HTML 앞부분: {soup.text[:200].strip()}")
        return []

    # 2. 날짜 인식 확인 (첫 3개만)
    print("DEBUG: 상위 3개 글 날짜 확인")
    for i, row in enumerate(rows[:3]):
        date_tag = row.select_one('.board_date')
        title_tag = row.select_one('font.list_title') or row.select_one('a')
        
        d_text = date_tag.text.strip() if date_tag else "없음"
        t_text = title_tag.text.strip() if title_tag else "없음"
        
        print(f"  Row {i+1}: 날짜=[{d_text}] / 제목=[{t_text}]")
        
    # 실제 수집 로직
    for row in rows:
        try:
            date_tag = row.select_one('.board_date')
            if not date_tag: continue
            date_text = date_tag.text.strip() 
            
            # 뽐뿌 날짜 매칭 (YY.MM.DD)
            if date_text == YESTERDAY_DOT:
                title_elem = row.select_one('font.list_title') or row.select_one('a')
                posts.append({
                    'source': 'ppomppu',
                    'title': title_elem.text.strip(),
                    'link': "https://www.ppomppu.co.kr/zboard/" + row.select_one('a')['href'],
                    'views': int(row.select_one('.board_hit').text.strip().replace(',', '')),
                    'comments': 0
                })
        except: continue
        
    print(f"👉 뽐뿌 수집 결과: {len(posts)}건")
    return posts

# --- [크롤러: 디시] ---
def get_dc_posts(driver):
    print("\n--- [디시 크롤링 진단] ---")
    posts = []
    url = "https://gall.dcinside.com/mgallery/board/lists/?id=mvno&page=1"
    
    driver.get(url)
    time.sleep(3)
    
    print(f"DEBUG: 페이지 제목 = {driver.title}")
    
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    rows = soup.select('tr.ub-content')
    
    print(f"DEBUG: 발견된 게시글 행(Row) 수 = {len(rows)}")
    
    if len(rows) == 0:
        print("❌ 게시글을 못 찾았습니다. (차단 가능성 높음)")
        return []

    # 날짜 인식 확인
    print("DEBUG: 상위 3개 글 날짜 확인")
    for i, row in enumerate(rows[:3]):
        if 'ub-notice' in row.get('class', []): continue
        date_tag = row.select_one('.gall_date')
        title_tag = row.select_one('.gall_tit > a')
        
        d_text = date_tag.text.strip() if date_tag else "없음"
        t_text = title_tag.text.strip() if title_tag else "없음"
        print(f"  Row {i+1}: 날짜=[{d_text}] / 제목=[{t_text}]")

    # 실제 수집
    for row in rows:
        try:
            if 'ub-notice' in row.get('class', []): continue
            date_tag = row.select_one('.gall_date')
            if not date_tag: continue
            
            # 디시 날짜 매칭 (MM.DD)
            if date_tag.text.strip() == YESTERDAY_SHORT:
                title_tag = row.select_one('.gall_tit > a')
                posts.append({
                    'source': 'dc',
                    'title': title_tag.text.strip(),
                    'link': "https://gall.dcinside.com" + title_tag['href'],
                    'views': int(row.select_one('.gall_count').text.strip().replace(',', '')),
                    'comments': 0
                })
        except: continue

    print(f"👉 디시 수집 결과: {len(posts)}건")
    return posts

# --- [메인] ---
def main():
    driver = get_driver()
    try:
        p_data = get_ppomppu_posts(driver)
        d_data = get_dc_posts(driver)
        
        total = len(p_data) + len(d_data)
        print(f"\n✅ 최종 합계: {total}건")
        
        # 파일 저장 (테스트용 - 0건이라도 일단 파일 생성해서 git 에러 방지)
        os.makedirs('data/monitoring', exist_ok=True)
        with open(f'data/monitoring/data_{YESTERDAY_FULL}.json', 'w', encoding='utf-8') as f:
            json.dump(p_data + d_data, f, indent=4, ensure_ascii=False)
        print("📁 (진단용) 강제로 JSON 파일 생성함.")
                
    except Exception as e:
        print(f"에러 발생: {e}")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
