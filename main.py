"""
[프로젝트] 경쟁사 프로모션 모니터링 자동화 시스템 (V24)
[작성자] 최지원 (GTM Strategy)
[업데이트] 2026-01-30 (무제한 전수조사 / 페이지네이션 부활 / 에러 무시 모드)
"""

import os
import json
import time
import glob
import re
import traceback
from datetime import datetime, timedelta, timezone
import requests
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# =========================================================
# [설정] 환경 변수
# =========================================================
GITHUB_USER = "rodolfochoi911-lgtm" 
REPO_NAME = "competitor-monitor" 
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL") 

DATA_DIR = "data"
DOCS_DIR = "docs"
REPORT_DIR = "docs/reports"

# [수정] 제한 해제 (Unlimited)
# MAX_ITEMS_PER_SITE = 9999 

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
FILE_TIMESTAMP = NOW.strftime("%Y%m%d_%H%M%S")
DISPLAY_DATE = NOW.strftime("%Y-%m-%d")
DISPLAY_TIME = NOW.strftime("%H:%M:%S")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

def setup_driver():
    print("🚗 브라우저 드라이버 설정 중...")
    chrome_options = Options()
    chrome_options.add_argument("--headless") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    print("✅ 드라이버 설정 완료")
    return driver

def remove_popups(driver):
    try:
        driver.execute_script("""
            var popups = document.querySelectorAll('.popup, .modal, .layer, .dimmed, .overlay, .toast, .banner, #popup, .close');
            popups.forEach(function(element) { element.remove(); });
        """)
    except: pass

def scroll_to_bottom(driver):
    try:
        last_height = driver.execute_script("return document.body.scrollHeight")
        for _ in range(5): # 충분히 스크롤
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height: break
            last_height = new_height
    except: pass

def clean_html(html_source):
    if not html_source: return ""
    soup = BeautifulSoup(html_source, 'html.parser')
    for tag in soup(['script', 'style', 'meta', 'noscript', 'header', 'footer', 'iframe', 'button', 'input', 'nav', 'aside']):
        tag.decompose()
    for hidden in soup.find_all(attrs={"style": True}):
        if "display:none" in hidden["style"].replace(" ", "").lower():
            hidden.decompose()
    return body.prettify() if (body := soup.find('body')) else "No Content"

def load_previous_data():
    json_files = glob.glob(os.path.join(DATA_DIR, "data_*.json"))
    if not json_files: return {}
    json_files.sort()
    latest_file = json_files[-1]
    print(f"📂 이전 데이터 로드: {os.path.basename(latest_file)}")
    try:
        with open(latest_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except: return {}

# [핵심] V24 카드 추출기 (무제한 + JS해독)
def extract_cards_smartly(driver, container_selector, site_name):
    cards_data = {} 
    try:
        print(f"    🔍 [{site_name}] 카드 요소 찾는 중...")
        container = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, container_selector))
        )
        
        items = []
        
        # 1. SKT 다이렉트
        if "SKT 다이렉트" in site_name:
            items = container.find_elements(By.TAG_NAME, "li")

        # 2. 유모바일
        elif "유모바일" in site_name:
            items = container.find_elements(By.XPATH, ".//li | .//div[contains(@class, 'card')]")
            if not items: items = container.find_elements(By.TAG_NAME, "li")

        # 3. 스카이라이프
        elif "스카이라이프" in site_name:
            items = container.find_elements(By.XPATH, "./div")

        # 4. [HELLOMOBILE]
        elif "헬로모바일" in site_name:
            try:
                list_ul = container.find_element(By.CSS_SELECTOR, ".event-list")
                items = list_ul.find_elements(By.TAG_NAME, "li")
            except: items = container.find_elements(By.TAG_NAME, "li")

        # 5. [SK 7MOBILE]
        elif "SK 7세븐모바일" in site_name:
            try:
                groups = container.find_elements(By.CSS_SELECTOR, ".event-group")
                for g in groups: items.extend(g.find_elements(By.TAG_NAME, "li"))
            except: items = container.find_elements(By.TAG_NAME, "li")

        # 6. KTM / 그 외
        else:
            items = container.find_elements(By.TAG_NAME, "li")
            if not items: items = container.find_elements(By.TAG_NAME, "a")

        if not items:
            print("    ⚠️ 아이템 없음 -> a 태그 비상 수집")
            items = container.find_elements(By.TAG_NAME, "a")

        print(f"    ✨ {len(items)}개 항목 발견 (전수조사)")

        for item in items:
            try:
                link_el = item if item.tag_name == 'a' else None
                if not link_el:
                    try: link_el = item.find_element(By.TAG_NAME, "a")
                    except: continue
                
                href = link_el.get_attribute('href')
                onclick = str(link_el.get_attribute('onclick')) 
                
                final_url = ""

                # [해독 1] 헬로모바일
                if "헬로모바일" in site_name and "fncEventDetail" in onclick:
                    match = re.search(r"fncEventDetail\((\d+)", onclick)
                    if match: final_url = f"https://direct.lghellovision.net/event/viewEventDetail.do?idxOfEvent={match.group(1)}"
                
                # [해독 2] SK 7모바일
                elif "SK 7세븐모바일" in site_name and "fnSearchView" in onclick:
                    match = re.search(r"fnSearchView\('([^']+)'", onclick)
                    if match: final_url = f"https://www.sk7mobile.com/bnef/event/eventIngView.do?cntId={match.group(1)}"
                
                # [일반]
                elif href and "javascript" not in href:
                    final_url = href
                elif href:
                    final_url = href

                if not final_url: continue

                title = item.text.strip().split("\n")[0]
                if not title:
                    try: title = item.find_element(By.TAG_NAME, "img").get_attribute("alt")
                    except: title = "제목 없음"
                
                img_src = ""
                try:
                    img = item.find_element(By.TAG_NAME, "img")
                    img_src = img.get_attribute("src")
                except: pass

                cards_data[final_url] = {"title": title, "img": img_src}
                
            except: continue
            
        return cards_data
    except Exception as e:
        print(f"    ❌ 카드 추출 중 에러: {e}")
        return {}

def extract_single_page_content(driver, selector):
    try:
        container = WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
        return {driver.current_url: {"title": "SKT Air 메인", "img": "", "content": clean_html(container.get_attribute('outerHTML'))}}
    except: return {}

def crawl_site_logic(driver, site_name, base_url, pagination_param=None, target_selector=None):
    print(f"🚀 [{site_name}] 데이터 수집 시작...")
    collected_items = {} 
    last_page_urls = []
    page = 1
    
    try:
        if site_name == "SKT Air":
            driver.get(base_url)
            time.sleep(3)
            return extract_single_page_content(driver, target_selector)

        # [부활] 페이지네이션 Loop (끝까지 감)
        while True:
            target_url = base_url
            if pagination_param:
                if pagination_param == "#": target_url = f"{base_url}#{page}"
                else: 
                    conn = '&' if '?' in base_url else '?'
                    target_url = f"{base_url}{conn}{pagination_param}={page}"
                
            driver.get(target_url)
            # 해시 방식은 새로고침 필요
            if pagination_param == "#": 
                driver.refresh()
                time.sleep(2)
            
            time.sleep(3)
            remove_popups(driver)
            scroll_to_bottom(driver)
            
            page_data = extract_cards_smartly(driver, target_selector, site_name)
            
            if not page_data: break # 데이터 없으면 종료
            
            # 절대경로 변환 & 저장
            current_page_urls = []
            for href, info in page_data.items():
                if href.startswith('/'):
                    from urllib.parse import urljoin
                    href = urljoin(base_url, href)
                
                # 이미 수집된 URL이면 건너뛰기 (중복 방지)
                if href in collected_items: continue
                
                collected_items[href] = info
                current_page_urls.append(href)

            print(f"  - Page {page}: {len(current_page_urls)}개 신규 수집")

            # 종료 조건 확인
            if not current_page_urls: break # 신규 데이터 없으면 종료
            if not pagination_param: break # 단일 페이지면 종료
            
            # 무한 루프 방지용 (안전장치가 아니라 사고 방지용)
            if sorted(current_page_urls) == sorted(last_page_urls): break
            last_page_urls = current_page_urls
            
            page += 1
            if page > 10: break # 그래도 10페이지 넘어가면 너무 많으니 끊음

    except Exception as e:
        print(f"  ❌ [{site_name}] 목록 수집 중 오류: {e}")

    print(f"  🔎 상세 분석 시작 ({len(collected_items)}건) - 전수조사 진행 중...")
    
    # [전수조사] 모든 상세 페이지 접속
    for url, info in collected_items.items():
        try:
            if "javascript" not in url:
                driver.get(url)
                time.sleep(1) # 접속 대기
                remove_popups(driver)
                collected_items[url]['content'] = clean_html(driver.page_source)
            else:
                collected_items[url]['content'] = "JS Link Only"
        except:
            collected_items[url]['content'] = "" 
            
    return collected_items

def main():
    try:
        driver = setup_driver()
        
        competitors = [
            {"name": "SKT 다이렉트", "url": "https://shop.tworld.co.kr/exhibition/submain", "param": None, "selector": "#wrap > div.container > div > div.event-list-wrap > div > ul"},
            {"name": "SKT Air", "url": "https://sktair-event.com/", "param": None, "selector": "#app > div > section.content"},
            {"name": "U+ 유모바일", "url": "https://www.uplusumobile.com/event-benefit/event/ongoing", "param": None, "selector": "#wrap > main > div > section"},
            {"name": "스카이라이프", "url": "https://www.skylife.co.kr/event?category=mobile", "param": "p", "selector": "body > div.pb-50.min-w-\[1248px\] > div.m-auto.max-w-\[1248px\].pt-20 > div > div > div.pt-14 > div > div.grid.grid-cols-3.gap-6.pt-4"},
            {"name": "헬로모바일", "url": "https://direct.lghellovision.net/event/viewEventList.do?returnTab=allli", "param": "#", "selector": ".event-list-wrap"},
            {"name": "SK 7세븐모바일", "url": "https://www.sk7mobile.com/bnef/event/eventIngList.do", "param": None, "selector": ".tb-list.bbs-card"},
            {"name": "KTM 모바일", "url": "https://www.ktmmobile.com/event/eventBoardList.do", "param": None, "selector": "#listArea1"}
        ]
        
        today_results = {}
        for comp in competitors:
            try:
                today_results[comp['name']] = crawl_site_logic(driver, comp['name'], comp['url'], comp['param'], comp['selector'])
            except Exception as e:
                print(f"❌ {comp['name']} 스킵됨 (에러): {e}")
        
        driver.quit()
        
        # 저장 (결과가 있든 없든 무조건 저장)
        data_filename = f"data_{FILE_TIMESTAMP}.json"
        with open(os.path.join(DATA_DIR, data_filename), "w", encoding="utf-8") as f:
            json.dump(today_results, f, ensure_ascii=False)
            
        print("✅ 모든 작업 완료! 리포트 생성 시작...")
        
        # 리포트 생성 로직 복원 (V19 버전 로직 사용)
        generate_report(today_results)

    except Exception as e:
        print("☠️ 프로그램 전체 크래시 발생!")
        print(traceback.format_exc())

# [복원] 리포트 생성 함수 (V19 로직 그대로)
def generate_report(today_results):
    yesterday_results = load_previous_data()
    report_body = ""
    total_change_count = 0
    company_summary = []
    
    for name, pages in today_results.items():
        site_changes = ""
        site_change_count = 0 
        old_pages = yesterday_results.get(name, {})
        all_urls = set(pages.keys()) | set(old_pages.keys())
        
        for url in all_urls:
            is_changed = False
            change_type = ""
            reason = ""
            curr = pages.get(url, {"title": "?", "img": "", "content": ""})
            prev = old_pages.get(url, {"title": "?", "img": "", "content": ""})
            
            if url in pages and url not in old_pages:
                is_changed = True; change_type = "NEW"; reason = "신규 이벤트"
            elif url not in pages and url in old_pages:
                is_changed = True; change_type = "DELETED"; reason = "종료된 이벤트"
            elif curr['content'].replace(" ","") != prev['content'].replace(" ",""):
                is_changed = True; change_type = "UPDATED"; reason = analyze_content_changes(prev['content'], curr['content'])

            if is_changed:
                color = "green" if change_type == "NEW" else "red" if change_type == "DELETED" else "orange"
                img_html = f"<img src='{curr['img']}' style='height:50px; vertical-align:middle; margin-right:10px;'>" if curr['img'] else ""
                site_changes += f"""<div style="border-left: 5px solid {color}; padding: 10px; margin-bottom: 10px; background: #fff;">
                    <h3 style="margin: 0 0 5px 0;"><span style="color:{color};">[{change_type}]</span> {curr['title']}</h3>
                    <div style="display:flex; align-items:center;">{img_html}<div style="font-size: 0.9em; color: #555;"><b>변경 사유:</b> {reason}<br><a href="{url}" target="_blank">🔗 바로가기</a></div></div></div>"""
                site_change_count += 1
        
        if site_changes:
            report_body += f"<h2>{name} ({site_change_count}건)</h2>{site_changes}<hr>"
            total_change_count += site_change_count
            company_summary.append(f"{name}({site_change_count})")

    # 리포트 파일 저장 및 슬랙 전송 (생략된 부분 복원)
    summary_text = f"총 {total_change_count}건 업데이트 ({', '.join(company_summary)})" if total_change_count > 0 else "특이사항 없음"
    report_header = f"<h1>📅 {DISPLAY_DATE} 리포트 <span style='font-size:0.6em; color:#888;'>({DISPLAY_TIME} KST)</span></h1><div style='background-color:#f4f4f4; padding:15px;'><h3>📊 {summary_text}</h3></div><hr>"
    full_report = report_header + (report_body if total_change_count > 0 else "<p>✅ 금일 변동 사항이 없습니다.</p>")
    
    filename = f"report_{FILE_TIMESTAMP}.html"
    with open(os.path.join(REPORT_DIR, filename), "w", encoding="utf-8") as f: f.write(full_report)
    update_index_page()
    
    dashboard_url = f"https://{GITHUB_USER}.github.io/{REPO_NAME}/"
    report_url = f"https://{GITHUB_USER}.github.io/{REPO_NAME}/reports/{filename}"
    
    payload = {"text": f"📢 *[KST {DISPLAY_TIME}] 경쟁사 동향 보고* \n\n✅ *요약:* {summary_text}\n\n👉 *변경 리포트:* {report_url}\n📂 *대시보드:* {dashboard_url}"}
    if SLACK_WEBHOOK_URL: requests.post(SLACK_WEBHOOK_URL, json=payload)

if __name__ == "__main__":
    main()
