import os
import json
import time
import random
import difflib
import requests
from datetime import datetime
from bs4 import BeautifulSoup

# [핵심] 깃허브 서버에서도 크롬 드라이버를 자동으로 잡아주는 라이브러리
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By

GITHUB_USER = "rodolfochoi911-lgtm"       # 예: rodolfochoi911-lgtm
REPO_NAME = "competitor-monitor" # 예: competitor-monitor

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL") 

DATA_DIR = "data"
REPORT_DIR = "docs/reports"
TODAY_STR = datetime.now().strftime("%Y-%m-%d")

# 폴더 없으면 자동 생성
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

def setup_driver():
    """깃허브 액션 서버용 헤드리스 크롬 설정 (자동 설치 적용)"""
    chrome_options = Options()
    chrome_options.add_argument("--headless") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    
    # [핵심] 드라이버 매니저가 알아서 설치하고 실행함 (버전 에러 해결!)
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def remove_popups(driver):
    """화면 가리는 팝업 강제 삭제"""
    try:
        driver.execute_script("""
            var popups = document.querySelectorAll('.popup, .modal, .layer, .dimmed, .overlay, .toast, .banner, #popup');
            popups.forEach(function(element) { element.remove(); });
        """)
    except:
        pass

def clean_html(html_source):
    """HTML 본문만 추출 (노이즈 제거)"""
    soup = BeautifulSoup(html_source, 'html.parser')
    for tag in soup(['script', 'style', 'meta', 'noscript', 'header', 'footer', 'iframe', 'button', 'input', 'nav', 'aside']):
        tag.decompose()
    body = soup.find('body')
    return body.prettify() if body else "No Content"

def crawl_site_logic(driver, site_name, base_url, pagination_param=None):
    print(f"🚀 [{site_name}] 분석 시작...")
    collected_links = []
    last_page_links = []
    page = 1
    
    while True:
        # URL 생성 (페이지네이션 처리)
        if pagination_param:
            connector = '&' if '?' in base_url else '?'
            target_url = f"{base_url}{connector}{pagination_param}={page}"
        else:
            target_url = base_url
            
        try:
            driver.get(target_url)
            time.sleep(2)
            remove_popups(driver)
            
            # 링크 추출 (a 태그 중 event, view 등이 포함된 것)
            links = driver.find_elements(By.TAG_NAME, "a")
            current_page_links = []
            
            for link in links:
                try:
                    href = link.get_attribute('href')
                    if href and ('event' in href or 'view' in href or 'detail' in href) and not href.startswith('#') and 'javascript' not in href:
                         # 상대경로 처리
                        if href.startswith('/'):
                            from urllib.parse import urljoin
                            href = urljoin(base_url, href)
                            
                        if href not in current_page_links:
                            current_page_links.append(href)
                except:
                    continue
            
            print(f"  - Page {page}: {len(current_page_links)}개 발견")

            if not current_page_links: break
            if sorted(current_page_links) == sorted(last_page_links): break # 중복(끝) 체크

            for lnk in current_page_links:
                if lnk not in collected_links:
                    collected_links.append(lnk)

            if not pagination_param: break # 단일 페이지는 1회만
            
            last_page_links = current_page_links
            page += 1
            if page > 10: break # 안전장치

        except Exception as e:
            print(f"  ⚠️ 오류 발생: {e}")
            break

    # 상세 페이지 딥다이브
    print(f"  🔎 상세 페이지 스캔 중 ({len(collected_links)}개)...")
    site_data = {}
    for link in collected_links: 
        try:
            driver.get(link)
            time.sleep(1)
            remove_popups(driver)
            site_data[link] = clean_html(driver.page_source)
        except:
            pass
            
    return site_data

def main():
    driver = setup_driver()
    
    competitors = [
        {"name": "SKT Shop", "url": "https://shop.tworld.co.kr/exhibition/submain", "param": None},
        {"name": "SKT Air", "url": "https://sktair-event.com/", "param": None},
        {"name": "KT M Mobile", "url": "https://www.ktmmobile.com/event/eventBoardList.do", "param": None},
        {"name": "U+ U Mobile", "url": "https://www.uplusumobile.com/event-benefit/event/ongoing", "param": None},
        {"name": "LG HelloVision", "url": "https://direct.lghellovision.net/event/viewEventList.do?returnTab=allli", "param": "pageIndex"},
        {"name": "Skylife", "url": "https://www.skylife.co.kr/event?category=mobile", "param": "page"}
    ]
    
    today_results = {}
    for comp in competitors:
        try:
            today_results[comp['name']] = crawl_site_logic(driver, comp['name'], comp['url'], comp['param'])
        except Exception as e:
            print(f"❌ {comp['name']} 실패: {e}")
    
    driver.quit()
    
    # 데이터 비교 및 리포트 작성
    latest_file = os.path.join(DATA_DIR, "latest_data.json")
    yesterday_results = {}
    if os.path.exists(latest_file):
        with open(latest_file, "r", encoding="utf-8") as f:
            yesterday_results = json.load(f)
            
    report_html = f"<h1>📅 {TODAY_STR} 경쟁사 리포트</h1><hr>"
    has_change = False
    
    for name, pages in today_results.items():
        site_changes = ""
        old_pages = yesterday_results.get(name, {})
        all_urls = set(pages.keys()) | set(old_pages.keys())
        
        for url in all_urls:
            if url in pages and url not in old_pages:
                has_change = True
                site_changes += f"<h3 style='color:green'>[NEW] <a href='{url}'>새 이벤트</a></h3><br>"
            elif url not in pages and url in old_pages:
                has_change = True
                site_changes += f"<h3 style='color:red'>[DELETED] <a href='{url}'>종료됨</a></h3><br>"
            elif pages[url].replace(" ","") != old_pages[url].replace(" ",""):
                has_change = True
                diff = difflib.HtmlDiff().make_table(old_pages[url].splitlines(), pages[url].splitlines(), context=True, numlines=3)
                site_changes += f"<h3 style='color:orange'>[UPDATED] <a href='{url}'>내용 변경</a></h3>{diff}<br>"
        
        if site_changes:
            report_html += f"<h2>{name}</h2>{site_changes}<hr>"

    if has_change:
        filename = f"report_{TODAY_STR}.html"
        with open(os.path.join(REPORT_DIR, filename), "w", encoding="utf-8") as f:
            f.write(report_html)
        with open(latest_file, "w", encoding="utf-8") as f:
            json.dump(today_results, f, ensure_ascii=False)
            
        # 슬랙 전송
        report_url = f"https://{GITHUB_USER}.github.io/{REPO_NAME}/reports/{filename}"
        payload = {"text": f"📢 *[{TODAY_STR}] 변동 감지!* \n리포트 확인: {report_url}"}
        
        if SLACK_WEBHOOK_URL:
            requests.post(SLACK_WEBHOOK_URL, json=payload)
            print("✅ 슬랙 알림 전송 완료")
        else:
            print("⚠️ 슬랙 URL 없음 (Secrets 설정 확인 필요)")
    else:
        print("✅ 변동 사항 없음")

if __name__ == "__main__":
    main()
