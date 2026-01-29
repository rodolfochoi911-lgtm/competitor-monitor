"""
[프로젝트] 경쟁사 프로모션 모니터링 자동화 시스템
[작성자] 최지원 (GTM Strategy)
[설명] 
주요 경쟁사(통신사/알뜰폰)의 이벤트 게시판을 정기적으로 크롤링하여,
신규 이벤트 런칭, 종료, 내용 변경(HTML Diff)을 감지하고 Slack으로 리포팅합니다.
GitHub Actions 환경에서 구동되도록 설계되었습니다.
"""

import os
import json
import time
import random
import difflib
import requests
from datetime import datetime
from bs4 import BeautifulSoup

# Selenium 및 Webdriver Manager 라이브러리 (브라우저 자동화)
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# =========================================================
# [설정] 환경 변수 및 리포지토리 정보 구성
# =========================================================
# 리포트 링크 생성을 위한 GitHub 계정 정보
GITHUB_USER = "rodolfochoi911-lgtm" 
REPO_NAME = "competitor-monitor" 

# Slack Webhook URL (GitHub Secrets 환경변수에서 로드하여 보안 유지)
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL") 

# 데이터 및 리포트 저장 경로 설정
DATA_DIR = "data"
REPORT_DIR = "docs/reports"
TODAY_STR = datetime.now().strftime("%Y-%m-%d")

# 필수 디렉토리가 없을 경우 자동 생성
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

def setup_driver():
    """
    [기능] Selenium Chrome Driver 초기화 및 설정
    [설명] GitHub Actions(Linux/Server) 환경에 맞춰 Headless 모드 및 필수 옵션을 적용합니다.
    """
    chrome_options = Options()
    chrome_options.add_argument("--headless")  # GUI 없이 백그라운드 실행
    chrome_options.add_argument("--no-sandbox") # 리눅스 환경 샌드박스 비활성화 (권한 문제 방지)
    chrome_options.add_argument("--disable-dev-shm-usage") # 메모리 공유 문제 방지
    # 봇 탐지 회피를 위한 User-Agent 설정 (일반 윈도우 환경으로 위장)
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    
    # Chrome Driver 자동 설치 및 서비스 실행 (버전 불일치 오류 방지)
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def remove_popups(driver):
    """
    [기능] 웹페이지 내 불필요한 레이어 제거
    [설명] 크롤링 시 클릭이나 데이터 수집을 방해하는 팝업, 모달, 배너 등을 JavaScript로 강제 제거합니다.
    """
    try:
        driver.execute_script("""
            var popups = document.querySelectorAll('.popup, .modal, .layer, .dimmed, .overlay, .toast, .banner, #popup');
            popups.forEach(function(element) { element.remove(); });
        """)
    except:
        pass # 팝업이 없는 경우 예외 무시

def clean_html(html_source):
    """
    [기능] HTML 데이터 전처리 (Data Cleaning)
    [설명] 비교 정확도를 높이기 위해 본문과 무관한 태그(Script, Style, Nav 등)를 제거합니다.
    """
    soup = BeautifulSoup(html_source, 'html.parser')
    
    # 분석에 불필요한 태그 목록 제거
    for tag in soup(['script', 'style', 'meta', 'noscript', 'header', 'footer', 'iframe', 'button', 'input', 'nav', 'aside']):
        tag.decompose()
        
    body = soup.find('body')
    # 전처리된 HTML을 문자열로 반환 (Body가 없을 경우 대체 텍스트 반환)
    return body.prettify() if body else "No Content"

def crawl_site_logic(driver, site_name, base_url, pagination_param=None, target_selector=None):
    """
    [기능] 개별 경쟁사 사이트 크롤링 로직 수행
    [파라미터]
      - driver: Selenium Driver 객체
      - site_name: 경쟁사명
      - base_url: 대상 URL
      - pagination_param: 페이지네이션 파라미터명 (None일 경우 단일 페이지)
      - target_selector: 감시 대상 CSS Selector (None일 경우 전체 페이지)
    """
    print(f"🚀 [{site_name}] 데이터 수집 프로세스 시작...")
    collected_links = []
    last_page_links = []
    page = 1
    
    # --- [Step 1] 이벤트 목록 수집 (페이지 순회) ---
    while True:
        # URL 파라미터 구성 (페이지네이션 처리)
        if pagination_param:
            connector = '&' if '?' in base_url else '?'
            target_url = f"{base_url}{connector}{pagination_param}={page}"
        else:
            target_url = base_url
            
        try:
            driver.get(target_url)
            time.sleep(3) # 동적 렌더링 대기
            remove_popups(driver)
            
            # 특정 영역(Selector)이 지정된 경우 해당 영역 로딩 대기 및 탐색
            if target_selector:
                try:
                    # 지정된 요소가 DOM에 로드될 때까지 최대 5초간 대기
                    container = WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, target_selector))
                    )
                    links = container.find_elements(By.TAG_NAME, "a")
                    print(f"  🎯 [타겟 감지] 지정된 영역({target_selector}) 내부 스캔 수행")
                except Exception as e:
                    print(f"  ⚠️ 타겟 영역 탐색 실패. 전체 범위 검색으로 전환합니다. ({e})")
                    links = driver.find_elements(By.TAG_NAME, "a")
            else:
                # Selector 미지정 시 전체 문서에서 탐색
                links = driver.find_elements(By.TAG_NAME, "a")

            current_page_links = []
            
            # 유효 링크 필터링 로직
            for link in links:
                try:
                    href = link.get_attribute('href')
                    # 이벤트, 공지사항 등 유의미한 키워드가 포함된 링크만 수집
                    if href and ('event' in href or 'view' in href or 'detail' in href or 'notice' in href) and not href.startswith('#') and 'javascript' not in href:
                        # 상대 경로를 절대 경로로 변환
                        if href.startswith('/'):
                            from urllib.parse import urljoin
                            href = urljoin(base_url, href)
                        
                        if href not in current_page_links:
                            current_page_links.append(href)
                except:
                    continue
            
            print(f"  - Page {page}: {len(current_page_links)}개 게시글 발견")

            # [탈출 조건 1] 게시글이 없는 경우 종료
            if not current_page_links: break
            
            # [탈출 조건 2] 이전 페이지와 결과가 동일한 경우 (마지막 페이지 도달) 종료
            if sorted(current_page_links) == sorted(last_page_links): break

            for lnk in current_page_links:
                if lnk not in collected_links:
                    collected_links.append(lnk)

            if not pagination_param: break # 단일 페이지 사이트는 1회 수행 후 종료
            
            last_page_links = current_page_links
            page += 1
            if page > 10: break # 무한 루프 방지를 위한 안전 장치 (최대 10페이지)

        except Exception as e:
            print(f"  ⚠️ 페이지 순회 중 예외 발생: {e}")
            break

    # --- [Step 2] 상세 페이지 딥다이브 (내용 수집) ---
    # [수정됨] 기존 5개 제한([:5])을 제거하여 전수 조사 수행
    print(f"  🔎 상세 페이지 정밀 분석 시작 (총 {len(collected_links)}건)...")
    site_data = {}
    
    for link in collected_links:
        try:
            driver.get(link)
            time.sleep(1) # 안정적인 데이터 수집을 위한 대기
            remove_popups(driver)
            site_data[link] = clean_html(driver.page_source)
        except Exception as e:
            print(f"  ❌ 상세 페이지 접근 실패: {link}")
            pass
            
    return site_data

def main():
    driver = setup_driver()
    
    # [설정] 경쟁사별 모니터링 타겟 URL 및 CSS Selector 정의
    competitors = [
        {
            "name": "SKT 다이렉트", 
            "url": "https://shop.tworld.co.kr/exhibition/submain", 
            "param": None, 
            "selector": "#wrap > div.container > div > div.event-list-wrap > div > ul"
        },
        {
            "name": "KTM 모바일", 
            "url": "https://www.ktmmobile.com/event/eventBoardList.do", 
            "param": None, 
            "selector": "#listArea1"
        },
        {
            "name": "U+ 유모바일", 
            "url": "https://www.uplusumobile.com/event-benefit/event/ongoing", 
            "param": None, 
            "selector": "#wrap > main > div > section"
        },
        {
            "name": "헬로모바일", 
            "url": "https://direct.lghellovision.net/event/viewEventList.do?returnTab=allli&category=USIM", 
            "param": "pageIndex", 
            "selector": "#contentWrap > div.event-list-wrap > section > div.list-wrap > ul"
        },
        {
            "name": "스카이라이프", 
            "url": "https://www.skylife.co.kr/event?category=mobile", 
            "param": "page", 
            "selector": "body > div.pb-50.min-w-\[1248px\] > div.m-auto.max-w-\[1248px\].pt-20 > div > div > div.pt-14 > div > div.grid.grid-cols-3.gap-6.pt-4"
        }
    ]
    
    today_results = {}
    
    # 경쟁사 순차 크롤링 수행
    for comp in competitors:
        try:
            today_results[comp['name']] = crawl_site_logic(driver, comp['name'], comp['url'], comp['param'], comp['selector'])
        except Exception as e:
            print(f"❌ [{comp['name']}] 크롤링 프로세스 중단: {e}")
    
    driver.quit() # 브라우저 리소스 해제
    
    # --- [Step 3] 데이터 비교 및 리포트 생성 ---
    latest_file = os.path.join(DATA_DIR, "latest_data.json")
    yesterday_results = {}
    
    # 이전 데이터 로드
    if os.path.exists(latest_file):
        with open(latest_file, "r", encoding="utf-8") as f:
            yesterday_results = json.load(f)
            
    report_body = ""
    total_change_count = 0
    company_summary = [] # 요약 정보 저장용 (예: SKT(2건))
    
    # 사이트별 데이터 비교 수행
    for name, pages in today_results.items():
        site_changes = ""
        site_change_count = 0 
        
        old_pages = yesterday_results.get(name, {})
        all_urls = set(pages.keys()) | set(old_pages.keys())
        
        for url in all_urls:
            is_changed = False
            change_html = ""
            
            # Case 1: 신규 이벤트 감지
            if url in pages and url not in old_pages:
                is_changed = True
                change_html = f"<h3 style='color:green'>[NEW] <a href='{url}' target='_blank'>새 이벤트 런칭</a></h3><br>"
            
            # Case 2: 이벤트 종료 감지
            elif url not in pages and url in old_pages:
                is_changed = True
                change_html = f"<h3 style='color:red'>[DELETED] <a href='{url}' target='_blank'>이벤트 종료</a></h3><br>"
            
            # Case 3: 내용 변경 감지 (HTML Diff)
            elif pages[url].replace(" ","") != old_pages[url].replace(" ",""):
                is_changed = True
                diff = difflib.HtmlDiff().make_table(old_pages[url].splitlines(), pages[url].splitlines(), context=True, numlines=3)
                change_html = f"<h3 style='color:orange'>[UPDATED] <a href='{url}' target='_blank'>상세 내용 변경</a></h3>{diff}<br>"
            
            if is_changed:
                site_changes += change_html
                site_change_count += 1
        
        if site_changes:
            report_body += f"<h2>{name} (변경 {site_change_count}건)</h2>{site_changes}<hr>"
            total_change_count += site_change_count
            company_summary.append(f"{name}({site_change_count}건)")

    # --- [Step 4] 결과 저장 및 알림 발송 ---
    
    # [수정됨] 변동 사항 유무와 관계없이 로직 분기 처리
    if total_change_count > 0:
        # 1) 변동 사항이 있을 경우: 리포트 생성 및 상세 알림 발송
        summary_text = f"총 {total_change_count}건 업데이트 ({', '.join(company_summary)})"
        
        report_header = f"""
        <h1>📅 {TODAY_STR} 경쟁사 프로모션 모니터링 리포트</h1>
        <div style='background-color:#f4f4f4; padding:15px; border-radius:10px; border:1px solid #ddd;'>
            <h3>📊 Executive Summary: {summary_text}</h3>
        </div>
        <hr>
        """
        full_report = report_header + report_body
        
        # 리포트 파일 저장
        filename = f"report_{TODAY_STR}.html"
        with open(os.path.join(REPORT_DIR, filename), "w", encoding="utf-8") as f:
            f.write(full_report)
            
        # 최신 데이터 갱신 (DB 업데이트)
        with open(latest_file, "w", encoding="utf-8") as f:
            json.dump(today_results, f, ensure_ascii=False)
            
        report_url = f"https://{GITHUB_USER}.github.io/{REPO_NAME}/reports/{filename}"
        
        # Slack 알림 (변동 발생 시)
        payload = {
            "text": f"📢 *[{TODAY_STR}] 경쟁사 동향 보고* \n\n✅ *요약:* {summary_text}\n👉 *상세 리포트 확인:* {report_url}"
        }
        
        if SLACK_WEBHOOK_URL:
            requests.post(SLACK_WEBHOOK_URL, json=payload)
            print("✅ [알림] 변동 사항 슬랙 전송 완료")
        else:
            print("⚠️ [경고] Slack Webhook URL이 설정되지 않았습니다.")
            
    else:
        # 2) 변동 사항이 없을 경우: "특이사항 없음" 알림 발송 (사용자 요청 반영)
        print("✅ 금일 변동 사항 없습니다.")
        
        # 데이터는 갱신 (오늘 날짜의 데이터가 최신 기준이 되도록)
        with open(latest_file, "w", encoding="utf-8") as f:
            json.dump(today_results, f, ensure_ascii=False)

        # Slack 알림 (변동 없을 시)
        payload = {
            "text": f"📋 *[{TODAY_STR}] 경쟁사 동향 보고* \n\n✅ 금일 감지된 경쟁사 프로모션 변동 사항/특이 사항 없습니다."
        }
        
        if SLACK_WEBHOOK_URL:
            requests.post(SLACK_WEBHOOK_URL, json=payload)
            print("✅ [알림] '변동 없음' 메시지 슬랙 전송 완료")

if __name__ == "__main__":
    main()
