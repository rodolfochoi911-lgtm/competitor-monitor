"""
[프로젝트] 경쟁사 프로모션 모니터링 자동화 시스템 (V56)
[작성자] 최지원 (GTM Strategy)
[업데이트] 2026-02-03 (전체 목록 썸네일 복구 + 변경 리포트 소스코드 뷰어 추가)
"""

import os
import json
import time
import glob
import random
import re
import traceback
import html  # [추가] 소스코드 출력을 위한 이스케이프 처리
from datetime import datetime, timedelta, timezone
import requests
from urllib.parse import urljoin
from bs4 import BeautifulSoup

# [핵심] 언디텍티드 크롬
import undetected_chromedriver as uc
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

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
FILE_TIMESTAMP = NOW.strftime("%Y%m%d_%H%M%S")
DISPLAY_DATE = NOW.strftime("%Y-%m-%d")
DISPLAY_TIME = NOW.strftime("%H:%M:%S")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

def setup_driver():
    print("🚗 [V56] 드라이버 설정 (버전 144 고정)...")
    options = uc.ChromeOptions()
    options.add_argument("--headless=new") 
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=ko_KR")
    
    driver = uc.Chrome(options=options, version_main=144)
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
        for _ in range(3): 
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(random.uniform(1.0, 2.0))
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height: break
            last_height = new_height
    except: pass

def clean_html(html_source):
    """
    HTML에서 스크립트 등 불필요한 태그 제거 후 'HTML 구조' 반환
    (V56 수정: 소스코드 뷰어를 위해 get_text() 대신 prettify() 사용)
    """
    if not html_source: return ""
    soup = BeautifulSoup(html_source, 'html.parser')
    # 제거할 태그 목록
    for tag in soup(['script', 'style', 'meta', 'noscript', 'header', 'footer', 'iframe', 'button', 'input', 'nav', 'aside', 'link', 'form']):
        tag.decompose()
    
    # 본문(Body)만 추출하거나 전체를 깔끔하게 반환
    if soup.body:
        return soup.body.prettify()
    return soup.prettify()

def load_previous_data():
    json_files = glob.glob(os.path.join(DATA_DIR, "data_*.json"))
    if not json_files: return {}
    json_files.sort()
    latest_file = json_files[-1]
    print(f"📂 어제 데이터 로드: {latest_file}")
    try:
        with open(latest_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except: return {}

def analyze_content_changes(prev, curr):
    """변경 사항 분석 및 사유 도출"""
    # 1. 제목 비교
    if prev.get('title', '').strip() != curr.get('title', '').strip():
        return f"✏️ 제목 변경: {prev.get('title')} -> {curr.get('title')}"
    
    # 2. 본문(HTML) 비교
    # 공백 제거 후 비교하여 불필요한 알림 최소화
    prev_clean = re.sub(r'\s+', '', prev.get('content', ''))
    curr_clean = re.sub(r'\s+', '', curr.get('content', ''))
    
    if prev_clean and curr_clean and prev_clean != curr_clean:
        return "📝 상세 본문 HTML 수정됨"
    
    # 3. 이미지 비교
    if prev.get('img', '').strip() != curr.get('img', '').strip():
        return "🖼️ 썸네일/이미지 변경"
            
    return None 

# =========================================================
# [Deep Crawler] 상세 수집
# =========================================================
def extract_deep_events(driver, site_name, keyword_list, onclick_pattern=None, base_url=""):
    collected_data = {}
    try:
        time.sleep(5)
        scroll_to_bottom(driver)
        
        if site_name == "스카이라이프":
            if "접속이 원활하지" in driver.page_source:
                print("    🚨 [Skylife] 차단됨 (목록 진입 불가).")
                return {}

        soup = BeautifulSoup(driver.page_source, 'html.parser')
        all_links = soup.find_all('a')
        target_urls = set()
        
        for link in all_links:
            href = link.get('href', '')
            onclick = link.get('onclick', '')
            final_url = ""

            if href and "javascript" not in href and "#" != href:
                for key in keyword_list:
                    if key in href:
                        final_url = urljoin(base_url, href)
                        break
            elif onclick and onclick_pattern:
                match = re.search(onclick_pattern, onclick)
                if match:
                    if site_name == "헬로모바일":
                        final_url = f"https://direct.lghellovision.net/event/viewEventDetail.do?idxOfEvent={match.group(1)}"
                    elif site_name == "SK 7세븐모바일":
                        final_url = f"https://www.sk7mobile.com/bnef/event/eventIngView.do?cntId={match.group(1)}"

            if site_name == "KTM 모바일" and not final_url:
                seq = link.get('ntcartseq')
                if seq: final_url = f"https://www.ktmmobile.com/event/eventDetail.do?ntcartSeq={seq}"

            if final_url:
                if any(x in final_url for x in ["login", "my", "faq", "support", "logout"]): continue
                target_urls.add(final_url)
        
        print(f"    [{site_name}] 발견된 상세 URL: {len(target_urls)}개 -> 상세 수집 시작")

        count = 0
        for url in target_urls:
            try:
                driver.get(url)
                time.sleep(random.uniform(2.0, 3.5))
                
                # [V56] HTML 소스 코드 저장 (clean_html이 태그 포함 반환)
                content_html = clean_html(driver.page_source)
                
                page_title = driver.title
                if not page_title or site_name in page_title: 
                    try: page_title = driver.find_element(By.TAG_NAME, "h1").text
                    except: 
                        try: page_title = driver.find_element(By.CSS_SELECTOR, "h2").text
                        except: page_title = "제목 없음"

                img_src = ""
                try:
                    meta_img = driver.find_element(By.CSS_SELECTOR, "meta[property='og:image']")
                    img_src = meta_img.get_attribute("content")
                except: pass
                
                collected_data[url] = {
                    "title": page_title.strip(),
                    "img": img_src,
                    "content": content_html[:10000] # HTML 저장 (너무 길면 자름)
                }
                count += 1
                print(f"      - [{count}/{len(target_urls)}] 수집 완료: {page_title[:20]}...")
                
                if count >= 20: break
                
            except Exception as e:
                print(f"      ❌ 상세 수집 실패 ({url}): {e}")
                continue
                
    except Exception as e:
        print(f"    ⚠️ {site_name} 목록 수집 실패: {e}")
        
    return collected_data


def extract_single_page_content(driver, selector):
    try:
        container = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
        return {driver.current_url: {"title": "SKT Air 메인", "img": "", "content": clean_html(container.get_attribute('outerHTML'))}}
    except: return {}

# =========================================================
# 통합 크롤링 로직
# =========================================================
def crawl_site_logic(driver, site_name, base_url, pagination_param=None, target_selector=None):
    print(f"🚀 [{site_name}] 시작...")
    
    if site_name == "SKT Air":
        driver.get(base_url); time.sleep(3)
        return extract_single_page_content(driver, target_selector)
    
    keywords = []
    onclick = None
    base = ""
    
    if site_name == "U+ 유모바일": keywords = ["event", "benefit"]; base = "https://www.uplusumobile.com"
    elif site_name == "KTM 모바일": keywords = ["eventDetail"]; base = "https://www.ktmmobile.com"
    elif site_name == "스카이라이프": keywords = ["/event/"]; base = "https://www.skylife.co.kr"
    elif site_name == "헬로모바일": keywords = ["event"]; onclick = r"(\d+)"; base = "https://direct.lghellovision.net"
    elif site_name == "SK 7세븐모바일": keywords = ["event"]; onclick = r"['\"]([^'\"]+)['\"]"; base = "https://www.sk7mobile.com"
    elif site_name == "SKT 다이렉트": keywords = ["event", "plan"]; base = "https://shop.tworld.co.kr"
    
    collected_items = {}
    page = 1
    
    while True:
        if pagination_param:
            if pagination_param == "#":
                target_url = f"{base_url}#{page}"
            else:
                separator = "&" if "?" in base_url else "?"
                target_url = f"{base_url}{separator}{pagination_param}={page}"
        else:
            target_url = base_url

        driver.get(target_url)
        if pagination_param == "#": driver.refresh(); time.sleep(2)
        
        time.sleep(3)
        remove_popups(driver)
        
        page_data = extract_deep_events(driver, site_name, keywords, onclick, base)
        
        new_cnt = 0
        for href, info in page_data.items():
            if href not in collected_items:
                collected_items[href] = info
                new_cnt += 1
        
        if new_cnt == 0: break
        if not pagination_param: break
        
        page += 1
        if page > 5: break

    return collected_items

def update_index_page():
    print("📊 대시보드(index.html) 업데이트 중...")
    report_files = glob.glob(os.path.join(REPORT_DIR, "report_*.html"))
    report_files.sort(reverse=True)
    
    index_html = f"""
    <html>
    <head>
        <meta charset="utf-8">
        <title>Competitor Promo Monitor</title>
        <style>
            body {{ font-family: sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }}
            h1 {{ color: #333; }}
            .report-link {{ display: block; padding: 10px; border-bottom: 1px solid #eee; text-decoration: none; color: #0066cc; }}
            .report-link:hover {{ background-color: #f9f9f9; }}
            .meta {{ color: #888; font-size: 0.8em; }}
        </style>
    </head>
    <body>
        <h1>📱 경쟁사 프로모션 모니터링</h1>
        <p class="meta">Last Update: {DISPLAY_DATE} {DISPLAY_TIME} (KST)</p>
        <hr>
        <h2>📄 리포트 목록</h2>
    """
    
    for f in report_files:
        name = os.path.basename(f)
        index_html += f"<a class='report-link' href='reports/{name}'>{name}</a>"
    
    index_html += "</body></html>"
    
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)
    
    with open(os.path.join(DOCS_DIR, ".nojekyll"), "w") as f:
        f.write("")

def main():
    try:
        driver = setup_driver()
        
        competitors = [
            {"name": "SKT 다이렉트", "url": "https://shop.tworld.co.kr/exhibition/submain", "param": None, "selector": ""},
            {"name": "SKT Air", "url": "https://sktair-event.com/", "param": None, "selector": "#app > div > section.content"},
            {"name": "U+ 유모바일", "url": "https://www.uplusumobile.com/event-benefit/event/ongoing", "param": None, "selector": ""},
            {"name": "KTM 모바일", "url": "https://www.ktmmobile.com/event/eventBoardList.do", "param": None, "selector": ""},
            {"name": "스카이라이프", "url": "https://www.skylife.co.kr/event?category=mobile", "param": "p", "selector": ""},
            {"name": "헬로모바일", "url": "https://direct.lghellovision.net/event/viewEventList.do?returnTab=allli", "param": "#", "selector": ""},
            {"name": "SK 7세븐모바일", "url": "https://www.sk7mobile.com/bnef/event/eventIngList.do", "param": None, "selector": ""}
        ]
        
        yesterday_results = load_previous_data()
        today_results = {}
        
        for comp in competitors:
            try:
                data = crawl_site_logic(driver, comp['name'], comp['url'], comp['param'], comp['selector'])
                
                if len(data) == 0:
                    print(f"    🛑 {comp['name']} 수집 실패(0건). 기존 데이터 유지.")
                    today_results[comp['name']] = yesterday_results.get(comp['name'], {})
                else:
                    today_results[comp['name']] = data
                    
            except Exception as e:
                print(f"❌ {comp['name']} Error: {e}")
                today_results[comp['name']] = yesterday_results.get(comp['name'], {})
        
        driver.quit()
        
        data_filename = f"data_{FILE_TIMESTAMP}.json"
        with open(os.path.join(DATA_DIR, data_filename), "w", encoding="utf-8") as f:
            json.dump(today_results, f, ensure_ascii=False)
            
        print("✅ 데이터 저장 완료")
        
        report_body = ""
        total_change_count = 0
        company_summary = []
        
        for name, pages in today_results.items():
            site_change_count = 0 
            old_pages = yesterday_results.get(name, {})
            all_urls = set(pages.keys()) | set(old_pages.keys())
            site_changes = ""
            
            for url in all_urls:
                change_type = ""; reason = ""
                curr = pages.get(url)
                prev = old_pages.get(url)

                if curr and not prev:
                    change_type = "NEW"; reason = "신규 이벤트"
                elif not curr and prev:
                    change_type = "DELETED"; reason = "종료된 이벤트"
                elif curr and prev:
                    diff_reason = analyze_content_changes(prev, curr)
                    if diff_reason:
                        change_type = "UPDATED"; reason = diff_reason

                if change_type:
                    color = "green" if change_type == "NEW" else "red" if change_type == "DELETED" else "orange"
                    img_src = curr.get('img') if curr else prev.get('img')
                    img_html = f"<img src='{img_src}' style='height:50px; margin-right:10px;'>" if img_src else ""
                    title = curr.get('title') if curr else prev.get('title')
                    
                    # [V56] 소스코드 뷰어 추가
                    source_code_html = ""
                    if change_type == "UPDATED" and curr.get('content'):
                         # HTML 이스케이프 처리하여 텍스트로 표시
                         safe_html = html.escape(curr.get('content')[:2000] + "...") 
                         source_code_html = f"""
                         <details style='margin-top:5px;'>
                            <summary style='cursor:pointer; color:#0066cc; font-size:0.9em;'>🔍 변경된 HTML 소스 보기</summary>
                            <pre style='background:#f4f4f4; padding:10px; font-size:0.8em; overflow-x:auto; border:1px solid #ddd;'>{safe_html}</pre>
                         </details>
                         """

                    site_changes += f"""
                    <div style="border-left: 5px solid {color}; padding: 10px; margin-bottom: 10px; background: #fff;">
                        <h3 style="margin: 0 0 5px 0;"><span style="color:{color};">[{change_type}]</span> {title}</h3>
                        <div style="display:flex; align-items:center;">
                            {img_html}
                            <div style="font-size: 0.9em; color: #555;"><b>사유:</b> {reason}<br><a href="{url}" target="_blank">🔗 링크</a></div>
                        </div>
                        {source_code_html}
                    </div>
                    """
                    site_change_count += 1
            
            if site_changes:
                report_body += f"<h2>{name} ({site_change_count}건)</h2>{site_changes}<hr>"
                total_change_count += site_change_count
                company_summary.append(f"{name}({site_change_count})")

        summary_text = f"총 {total_change_count}건 변동 ({', '.join(company_summary)})" if total_change_count > 0 else "특이사항 없음"
        report_header = f"<h1>📅 {DISPLAY_DATE} 리포트</h1><div><h3>📊 {summary_text}</h3></div><hr>"
        
        filename = f"report_{FILE_TIMESTAMP}.html"
        with open(os.path.join(REPORT_DIR, filename), "w", encoding="utf-8") as f: f.write(report_header + report_body)
        
        update_index_page()
        
        # [V56] 전체 목록(list_*.html) 디자인 업그레이드 (썸네일 + 그리드)
        full_list_html = f"<h1>📂 {DISPLAY_DATE} 전체 목록</h1><hr>"
        for name, pages in today_results.items():
            full_list_html += f"<h3>{name} ({len(pages)}개)</h3><div style='display:grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap:15px;'>"
            for url, data in pages.items():
                img_tag = f"<img src='{data.get('img','')}' style='width:100%; height:120px; object-fit:cover; border-radius:5px; border:1px solid #eee;'>" if data.get('img') else "<div style='width:100%; height:120px; background:#f0f0f0; display:flex; align-items:center; justify-content:center; border-radius:5px;'>No Image</div>"
                full_list_html += f"""
                <div style='border:1px solid #ddd; padding:10px; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.05);'>
                    <a href='{url}' target='_blank' style='text-decoration:none; color:#333;'>
                        {img_tag}
                        <p style='margin:10px 0 0 0; font-weight:bold; font-size:0.95em; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;'>{data.get('title')}</p>
                    </a>
                </div>
                """
            full_list_html += "</div><hr>"
            
        list_filename = f"list_{FILE_TIMESTAMP}.html"
        with open(os.path.join(REPORT_DIR, list_filename), "w", encoding="utf-8") as f: f.write(full_list_html)

        dashboard_url = f"https://{GITHUB_USER}.github.io/{REPO_NAME}/"
        report_url = f"https://{GITHUB_USER}.github.io/{REPO_NAME}/reports/{filename}"
        list_url = f"https://{GITHUB_USER}.github.io/{REPO_NAME}/reports/{list_filename}"
        
        payload = {"text": f"📢 *[KST {DISPLAY_TIME}] 경쟁사 동향 보고* \n\n✅ *요약:* {summary_text}\n\n👉 *변경 리포트:* {report_url}\n🗂️ *전체 목록:* {list_url}\n📂 *대시보드:* {dashboard_url}"}
        
        if SLACK_WEBHOOK_URL:
            requests.post(SLACK_WEBHOOK_URL, json=payload)
            print("✅ 슬랙 전송 완료")

    except Exception as e:
        print(f"🔥 Critical Error: {traceback.format_exc()}")

if __name__ == "__main__":
    main()
