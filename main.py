"""
[프로젝트] 경쟁사 프로모션 모니터링 자동화 시스템 (V58)
[작성자] 최지원 (GTM Strategy)
[업데이트] 2026-02-03 (대시보드 고도화: '오늘의 변동(스택바)' + '7일간 추이(라인차트)' 시각화 적용)
"""

import os
import json
import time
import glob
import random
import re
import traceback
import html
import difflib
import requests
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
from bs4 import BeautifulSoup
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
SIMILARITY_THRESHOLD = 0.8

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
FILE_TIMESTAMP = NOW.strftime("%Y%m%d_%H%M%S")
DISPLAY_DATE = NOW.strftime("%Y-%m-%d")
DISPLAY_TIME = NOW.strftime("%H:%M:%S")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

def setup_driver():
    print("🚗 [V58] 드라이버 설정 (버전 144)...")
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
    if not html_source: return ""
    soup = BeautifulSoup(html_source, 'html.parser')
    for tag in soup(['script', 'style', 'meta', 'noscript', 'header', 'footer', 'iframe', 'button', 'input', 'nav', 'aside', 'link', 'form']):
        tag.decompose()
    return soup.body.prettify() if soup.body else soup.prettify()

def get_clean_text(html_content):
    if not html_content: return ""
    return BeautifulSoup(html_content, "html.parser").get_text(separator=" ", strip=True)

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

def calculate_similarity(text1, text2):
    if not text1 or not text2: return 0.0
    return difflib.SequenceMatcher(None, text1, text2).ratio()

# Side-by-Side Diff 생성
def check_update_same_url(prev, curr):
    reasons = []
    diff_html = ""
    
    if prev.get('title', '').strip() != curr.get('title', '').strip():
        reasons.append("제목 변경")
    
    prev_txt = get_clean_text(prev.get('content', ''))
    curr_txt = get_clean_text(curr.get('content', ''))
    
    if prev_txt and curr_txt:
        sim = calculate_similarity(prev_txt, curr_txt)
        if sim < 1.0: 
            reasons.append("본문 수정")
            diff_html = f"""
            <div style="display:flex; gap:10px; margin-top:10px;">
                <div style="flex:1; background:#ffeef0; padding:10px; border-radius:5px;">
                    <strong style="color:red;">[이전]</strong>
                    <div style="font-size:13px; line-height:1.4; max-height:200px; overflow-y:auto;">{html.escape(prev_txt[:500])}...</div>
                </div>
                <div style="flex:1; background:#e6fffa; padding:10px; border-radius:5px;">
                    <strong style="color:green;">[현재]</strong>
                    <div style="font-size:13px; line-height:1.4; max-height:200px; overflow-y:auto;">{html.escape(curr_txt[:500])}...</div>
                </div>
            </div>
            """

    if prev.get('img', '').strip() != curr.get('img', '').strip():
        reasons.append("썸네일 변경")
            
    if reasons: 
        return {"msg": f"{', '.join(reasons)}", "html": diff_html}
    return None

# =========================================================
# [Deep Crawler] 상세 수집
# =========================================================
def extract_deep_events(driver, site_name, keyword_list, onclick_pattern=None, base_url=""):
    collected_data = {}
    try:
        time.sleep(4)
        scroll_to_bottom(driver)
        
        if "접속이 원활하지" in driver.page_source or "Access Denied" in driver.page_source:
            print(f"    🚨 [{site_name}] 차단됨.")
            return {}

        soup = BeautifulSoup(driver.page_source, 'html.parser')
        all_links = soup.find_all('a')
        target_urls = set()
        exclude_keywords = ["winner", "notice", "dangcheom", "end", "fin", "past", "당첨", "종료", "발표", "공지"]

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
                if any(ex in final_url.lower() for ex in exclude_keywords): continue
                if site_name == "U+ 유모바일" and "종료" in link.get_text(): continue
                target_urls.add(final_url)
        
        print(f"    [{site_name}] 상세 URL: {len(target_urls)}개 -> 수집 시작")

        count = 0
        for url in target_urls:
            try:
                driver.get(url)
                time.sleep(random.uniform(1.5, 3.0))
                
                if "404" in driver.title or "페이지를 찾을 수" in driver.page_source: continue

                content_html = clean_html(driver.page_source)
                page_title = ""
                
                try: page_title = driver.find_element(By.TAG_NAME, "h1").text.strip()
                except: pass
                
                if not page_title or site_name in page_title:
                    try: page_title = driver.find_element(By.CSS_SELECTOR, ".view-tit, .event-view-title, .board-view-title, h2").text.strip()
                    except: pass
                if not page_title: page_title = driver.title.strip()
                
                img_src = ""
                try:
                    meta_img = driver.find_element(By.CSS_SELECTOR, "meta[property='og:image']")
                    img_src = meta_img.get_attribute("content")
                except:
                    try:
                        imgs = driver.find_elements(By.CSS_SELECTOR, "div.content img, div.view_content img")
                        for i in imgs:
                            if i.size['width'] > 200:
                                img_src = i.get_attribute("src")
                                break
                    except: pass

                if "당첨" in page_title or "발표" in page_title: continue

                collected_data[url] = {
                    "title": page_title,
                    "img": img_src,
                    "content": content_html[:15000] 
                }
                count += 1
                if count >= 60: break
            except: continue
    except: pass
    return collected_data

def extract_single_page_content(driver, selector):
    try:
        container = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
        return {driver.current_url: {"title": "SKT Air 메인", "img": "", "content": clean_html(container.get_attribute('outerHTML'))}}
    except: return {}

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
    max_page = 5 if site_name != "SKT 다이렉트" else 10
    
    while True:
        if pagination_param:
            if pagination_param == "#": target_url = f"{base_url}#{page}"
            else:
                separator = "&" if "?" in base_url else "?"
                target_url = f"{base_url}{separator}{pagination_param}={page}"
        else: target_url = base_url

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
        if page > max_page: break

    return collected_items

# =========================================================
# [대시보드] 차트 시각화 (변동 현황 + 시계열 추이)
# =========================================================
def update_index_page(change_stats):
    print("📊 대시보드(차트) 업데이트 중...")
    report_files = glob.glob(os.path.join(REPORT_DIR, "report_*.html"))
    report_files.sort(reverse=True)
    
    # 1. 차트 데이터 준비: 오늘 변동 현황 (Stacked Bar)
    labels = list(change_stats.keys())
    new_counts = [v['new'] for v in change_stats.values()]
    updated_counts = [v['updated'] for v in change_stats.values()]
    deleted_counts = [v['deleted'] for v in change_stats.values()]

    # 2. 차트 데이터 준비: 7일간 추이 (Line Chart)
    # 과거 7일 데이터 로드
    history_files = sorted(glob.glob(os.path.join(DATA_DIR, "data_*.json")))[-7:]
    history_dates = []
    history_series = {name: [] for name in labels}
    
    for h_file in history_files:
        try:
            # 파일명에서 날짜 추출 (data_20240203_120000.json -> 02/03)
            date_str = os.path.basename(h_file).split('_')[1]
            formatted_date = f"{date_str[4:6]}/{date_str[6:8]}"
            history_dates.append(formatted_date)
            
            with open(h_file, 'r', encoding='utf-8') as f:
                d = json.load(f)
                for name in labels:
                    history_series[name].append(len(d.get(name, {})))
        except: pass

    # 시계열 데이터셋 생성 (색상 팔레트 사용)
    colors = ['#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', '#FF9F40', '#E7E9ED']
    line_datasets = []
    for idx, (name, data) in enumerate(history_series.items()):
        line_datasets.append({
            "label": name,
            "data": data,
            "borderColor": colors[idx % len(colors)],
            "fill": False,
            "tension": 0.1
        })

    chart_script = f"""
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script>
        // Chart 1: 오늘의 변동 현황 (Stacked Bar)
        const ctx1 = document.getElementById('changeChart');
        new Chart(ctx1, {{
            type: 'bar',
            data: {{
                labels: {json.dumps(labels)},
                datasets: [
                    {{ label: '🟢 신규', data: {json.dumps(new_counts)}, backgroundColor: 'rgba(75, 192, 192, 0.7)' }},
                    {{ label: '🟠 변경', data: {json.dumps(updated_counts)}, backgroundColor: 'rgba(255, 159, 64, 0.7)' }},
                    {{ label: '🔴 종료', data: {json.dumps(deleted_counts)}, backgroundColor: 'rgba(255, 99, 132, 0.7)' }}
                ]
            }},
            options: {{
                responsive: true,
                scales: {{ x: {{ stacked: true }}, y: {{ stacked: true }} }},
                plugins: {{ legend: {{ position: 'top' }} }}
            }}
        }});

        // Chart 2: 7일간 이벤트 총량 추이 (Line)
        const ctx2 = document.getElementById('trendChart');
        new Chart(ctx2, {{
            type: 'line',
            data: {{
                labels: {json.dumps(history_dates)},
                datasets: {json.dumps(line_datasets)}
            }},
            options: {{
                responsive: true,
                plugins: {{ legend: {{ position: 'bottom' }} }}
            }}
        }});
    </script>
    """

    index_html = f"""
    <html>
    <head>
        <meta charset="utf-8">
        <title>Competitor Promo Monitor</title>
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px; background-color:#f5f7fa; }}
            .card {{ background:white; padding:25px; border-radius:12px; box-shadow:0 4px 6px rgba(0,0,0,0.05); margin-bottom:25px; }}
            h1 {{ color: #2c3e50; margin-bottom: 5px; }}
            h3 {{ color: #34495e; border-bottom: 2px solid #eee; padding-bottom: 10px; margin-top: 0; }}
            .report-link {{ display: block; padding: 12px; border-bottom: 1px solid #f0f0f0; text-decoration: none; color: #3498db; font-weight:500; transition:0.2s; }}
            .report-link:hover {{ background-color: #f0f8ff; padding-left: 15px; }}
            .grid-container {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
            @media (max-width: 768px) {{ .grid-container {{ grid-template-columns: 1fr; }} }}
        </style>
    </head>
    <body>
        <div style="text-align:center; margin-bottom:30px;">
            <h1>📱 경쟁사 프로모션 관제탑</h1>
            <p style="color:#7f8c8d;">Last Update: {DISPLAY_DATE} {DISPLAY_TIME} (KST)</p>
        </div>
        
        <div class="grid-container">
            <div class="card">
                <h3>📊 오늘 변동 현황 (New/Mod/Del)</h3>
                <canvas id="changeChart"></canvas>
            </div>
            <div class="card">
                <h3>📈 7일간 이벤트 규모 추이</h3>
                <canvas id="trendChart"></canvas>
            </div>
        </div>
        
        <div class="card">
            <h3>🗂️ 리포트 아카이브</h3>
            <div style="max-height: 400px; overflow-y: auto;">
                {''.join([f"<a href='reports/{os.path.basename(f)}' class='report-link'>{os.path.basename(f)}</a>" for f in report_files])}
            </div>
        </div>
        {chart_script}
    </body>
    </html>
    """
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f: f.write(index_html)
    with open(os.path.join(DOCS_DIR, ".nojekyll"), "w") as f: f.write("")

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
        # 대시보드용 변동 통계 (신규/수정/삭제 건수)
        change_stats = {comp['name']: {'new': 0, 'updated': 0, 'deleted': 0} for comp in competitors}
        
        for comp in competitors:
            try:
                data = crawl_site_logic(driver, comp['name'], comp['url'], comp['param'], comp['selector'])
                if len(data) == 0: today_results[comp['name']] = yesterday_results.get(comp['name'], {})
                else: today_results[comp['name']] = data
            except: today_results[comp['name']] = yesterday_results.get(comp['name'], {})
        
        driver.quit()
        
        with open(os.path.join(DATA_DIR, f"data_{FILE_TIMESTAMP}.json"), "w", encoding="utf-8") as f:
            json.dump(today_results, f, ensure_ascii=False)
            
        report_body = ""
        total_change_count = 0
        company_summary = []
        
        for name, pages in today_results.items():
            site_change_count = 0 
            old_pages = yesterday_results.get(name, {})
            
            common_urls = set(pages.keys()) & set(old_pages.keys())
            new_candidate_urls = set(pages.keys()) - set(old_pages.keys())
            deleted_candidate_urls = set(old_pages.keys()) - set(pages.keys())
            
            list_new = []
            list_updated = []
            list_deleted = []

            # 1. Update Check
            for url in common_urls:
                diff = check_update_same_url(old_pages[url], pages[url])
                if diff:
                    list_updated.append({"url": url, "reason": diff['msg'], "data": pages[url], "diff_html": diff['html']})

            # 2. Similarity Check
            real_new = []
            real_deleted = list(deleted_candidate_urls)

            for new_url in new_candidate_urls:
                is_moved = False
                new_item = pages[new_url]
                for old_url in list(real_deleted):
                    old_item = old_pages[old_url]
                    total_score = (calculate_similarity(new_item.get('title'), old_item.get('title')) * 0.4) + \
                                  (calculate_similarity(get_clean_text(new_item.get('content')), get_clean_text(old_item.get('content'))) * 0.6)
                    
                    if total_score >= SIMILARITY_THRESHOLD:
                        list_updated.append({"url": new_url, "reason": f"🔗 링크 변경 (유사도 {int(total_score*100)}%)", "data": new_item, "diff_html": ""})
                        real_deleted.remove(old_url)
                        is_moved = True
                        break
                if not is_moved: real_new.append(new_url)

            # 3. Finalize Lists
            for url in real_new: list_new.append({"url": url, "data": pages[url]})
            for url in real_deleted: list_deleted.append({"url": url, "data": old_pages[url]})

            # 4. 통계 집계 (for Chart 1)
            change_stats[name]['new'] = len(list_new)
            change_stats[name]['updated'] = len(list_updated)
            change_stats[name]['deleted'] = len(list_deleted)

            # 5. HTML Generation
            site_html = ""
            if list_new:
                site_html += f"<h3 style='color:green;'>🟢 신규 ({len(list_new)}건)</h3>"
                for item in list_new:
                    data = item['data']
                    img = f"<img src='{data.get('img')}' style='height:80px; margin-right:15px; border-radius:4px;'>" if data.get('img') else ""
                    site_html += f"<div style='padding:15px; background:#f9fff9; border:1px solid #ccffcc; border-radius:5px; display:flex; margin-bottom:10px;'>{img}<div><b>{data.get('title')}</b><br><a href='{item['url']}' target='_blank'>🔗 바로가기</a></div></div>"

            if list_updated:
                site_html += f"<h3 style='color:orange;'>🟠 변경 ({len(list_updated)}건)</h3>"
                for item in list_updated:
                    data = item['data']
                    diff_view = item.get('diff_html', '')
                    site_html += f"<div style='padding:15px; background:#fffcf5; border:1px solid #ffebcc; border-radius:5px; margin-bottom:10px;'><b>{data.get('title')}</b><br><span style='color:#666;'>{item['reason']}</span><br><a href='{item['url']}' target='_blank'>🔗 바로가기</a>{diff_view}</div>"

            if list_deleted:
                site_html += f"<h3 style='color:red;'>🔴 종료 ({len(list_deleted)}건)</h3>"
                for item in list_deleted:
                    data = item['data']
                    site_html += f"<div style='padding:15px; background:#fff5f5; border:1px solid #ffcccc; border-radius:5px; margin-bottom:10px; opacity:0.7;'><b style='text-decoration:line-through;'>{data.get('title')}</b></div>"

            cnt = len(list_new) + len(list_updated) + len(list_deleted)
            if cnt > 0:
                report_body += f"<div style='margin-bottom:40px;'><h2>{name} ({cnt}건)</h2>{site_html}</div><hr>"
                total_change_count += cnt
                company_summary.append(f"{name}({cnt})")

        # 6. Final Outputs
        summary_text = f"총 {total_change_count}건 변동 ({', '.join(company_summary)})" if total_change_count > 0 else "특이사항 없음"
        report_header = f"<h1>📅 {DISPLAY_DATE} 리포트</h1><div><h3>📊 {summary_text}</h3></div><hr>"
        filename = f"report_{FILE_TIMESTAMP}.html"
        
        with open(os.path.join(REPORT_DIR, filename), "w", encoding="utf-8") as f: f.write(report_header + report_body)
        
        # [V58] 대시보드(차트) 업데이트 호출 (통계 전달)
        update_index_page(change_stats)
        
        # 전체 목록 (그리드)
        full_list_html = f"<h1>📂 {DISPLAY_DATE} 전체 목록</h1><hr>"
        for name, pages in today_results.items():
            full_list_html += f"<h3>{name} ({len(pages)}개)</h3><div style='display:grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap:15px;'>"
            for url, data in pages.items():
                img = f"<img src='{data.get('img','')}' style='width:100%; height:120px; object-fit:cover; border-radius:5px; border:1px solid #eee;'>" if data.get('img') else "<div style='width:100%; height:120px; background:#f0f0f0; display:flex; align-items:center; justify-content:center; border-radius:5px;'>No Image</div>"
                full_list_html += f"<div style='border:1px solid #ddd; padding:10px; border-radius:8px;'><a href='{url}' target='_blank' style='text-decoration:none; color:#333;'>{img}<p style='margin:10px 0 0 0; font-weight:bold; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;'>{data.get('title')}</p></a></div>"
            full_list_html += "</div><hr>"
        
        list_filename = f"list_{FILE_TIMESTAMP}.html"
        with open(os.path.join(REPORT_DIR, list_filename), "w", encoding="utf-8") as f: f.write(full_list_html)

        dashboard_url = f"https://{GITHUB_USER}.github.io/{REPO_NAME}/"
        report_url = f"https://{GITHUB_USER}.github.io/{REPO_NAME}/reports/{filename}"
        list_url = f"https://{GITHUB_USER}.github.io/{REPO_NAME}/reports/{list_filename}"
        
        payload = {"text": f"📢 *[KST {DISPLAY_TIME}] 경쟁사 동향 보고* \n\n✅ *요약:* {summary_text}\n\n👉 *변경 리포트:* {report_url}\n🗂️ *전체 목록:* {list_url}\n📂 *대시보드:* {dashboard_url}"}
        if SLACK_WEBHOOK_URL: requests.post(SLACK_WEBHOOK_URL, json=payload)
        print("✅ 완료")

    except Exception as e: print(f"🔥 Error: {traceback.format_exc()}")

if __name__ == "__main__":
    main()

