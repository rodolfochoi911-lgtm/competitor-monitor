"""
[프로젝트] 경쟁사 프로모션 모니터링 자동화 시스템
[버전] V103 (본문 + 유의사항 동적 추출 추가, 스카이라이프 전용 파서 적용)
"""

import os
import json
import time
import re
import requests
import subprocess
import pandas as pd
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By

from content_extractor import extract_page_content

# =========================================================
# 설정
# =========================================================
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")
DATA_DIR = "data"

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
FILE_TIMESTAMP = NOW.strftime("%Y%m%d_%H%M%S")

os.makedirs(DATA_DIR, exist_ok=True)

EXCLUDE_URL_KEYWORDS = [
    "login", "my", "faq", "logout", "support",
    "notice", "news", "winner", "error.html", "submain"
]
EXCLUDE_TITLE_KEYWORDS = ["[종료]", "종료된", "당첨자", "발표", "개인정보", "이용약관", "유의사항", "다모아 결합 시즌2"]

BAD_TITLES = [
    "진행 이벤트", "지난 이벤트", "종료된 이벤트", "당첨자 발표", "이벤트",
    "SK 7mobile", "KT M모바일", "LG HelloVision", "스카이라이프", "SKT Tworld",
    "친구 추천", "이번달 이벤트", "월 이벤트", "이달의 이벤트"
]
SK7_BAD_TITLES = BAD_TITLES + [
    "주메뉴", "바로가기", "본문 바로가기", "TOP", "전체 메뉴", "로그인", "회원가입"
]

# =========================================================
# 유틸리티
# =========================================================
def send_slack_alert(message: str):
    if not SLACK_WEBHOOK_URL:
        return
    try:
        requests.post(SLACK_WEBHOOK_URL, json={"text": message}, timeout=10)
    except Exception as e:
        print(f"⚠️ 슬랙 전송 실패: {e}")


def clean_html(html_source: str) -> str:
    if not html_source:
        return ""
    soup = BeautifulSoup(html_source, 'html.parser')
    for tag in soup(['script', 'style', 'meta', 'noscript', 'header',
                     'footer', 'iframe', 'button', 'input', 'nav', 'aside']):
        tag.decompose()
    return soup.body.prettify() if soup.body else soup.prettify()


def clean_title(title: str) -> str:
    """제목에서 D-day / 취소선 / 날짜범위 잔재 제거"""
    title = re.sub(r'\s*D-\d+', '', title)
    title = re.sub(r'~~[^~]+~~', '', title)
    title = re.sub(r'\d{4}-\d{2}-\d{2}~\d{4}-\d{2}-\d{2}', '', title)
    title = re.sub(r'\d{4}\.\d{2}\.\d{2}~\d{4}\.\d{2}\.\d{2}', '', title)
    title = re.sub(r'\s*\d+일\s*남음', '', title)
    return re.sub(r'\s{2,}', ' ', title).strip()


# =========================================================
# 드라이버
# =========================================================
def get_chrome_version():
    """설치된 Chrome 메이저 버전 자동 감지"""
    for cmd in [
        ["google-chrome", "--version"],
        ["google-chrome-stable", "--version"],
        ["chromium-browser", "--version"],
        ["chromium", "--version"],
    ]:
        try:
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode()
            m = re.search(r'(\d+)\.\d+\.\d+', out)
            if m:
                return int(m.group(1))
        except Exception:
            continue
    return None


def setup_driver() -> uc.Chrome:
    options = uc.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-infobars")
    options.add_argument("--log-level=3")
    options.add_argument("--blink-settings=imagesEnabled=false")
    options.page_load_strategy = 'eager'

    chrome_ver = get_chrome_version()
    if chrome_ver:
        print(f"   🔍 Chrome 버전 감지: {chrome_ver}")
        driver = uc.Chrome(options=options, version_main=chrome_ver)
    else:
        print("   ⚠️ Chrome 버전 감지 실패 — uc 자동 감지 사용")
        driver = uc.Chrome(options=options)

    driver.set_page_load_timeout(30)
    driver.implicitly_wait(0)
    return driver


# =========================================================
# 크롤러 — 목록 수집
# =========================================================
def extract_list_with_thumbnails(
    driver, site_name: str, keyword_list: list,
    onclick_pattern=None, base_url: str = "", target_selector=None
) -> dict:
    targets = {}
    skipped_no_thumb = 0

    try:
        time.sleep(2)
        soup = BeautifulSoup(driver.page_source, 'html.parser')

        if site_name == "SK 7세븐모바일":
            area = soup.select_one("#ct > section")
            if area:
                soup = area

        for link in soup.find_all('a'):
            href = link.get('href', '')
            onclick = link.get('onclick', '')
            final_url = ""
            list_title_text = ""

            if site_name == "KTM 모바일":
                seq = link.get('ntcartSeq') or link.get('ntcartseq')
                if seq and str(seq) != "1680":
                    final_url = f"https://www.ktmmobile.com/event/eventDetail.do?ntcartSeq={seq}"
                    sub = link.find(class_="event-list__title__sub")
                    if sub:
                        list_title_text = sub.get_text(strip=True)
                else:
                    continue
            else:
                if href and "javascript" not in href and href != "#":
                    for key in keyword_list:
                        if key in href:
                            final_url = urljoin(base_url, href)
                            break
                elif onclick and onclick_pattern:
                    m = re.search(onclick_pattern, onclick)
                    if m:
                        if site_name == "헬로모바일":
                            final_url = (
                                f"https://direct.lghellovision.net/event/"
                                f"viewEventDetail.do?idxOfEvent={m.group(1)}"
                            )
                        elif site_name == "SK 7세븐모바일":
                            final_url = (
                                f"https://www.sk7mobile.com/bnef/event/"
                                f"eventIngView.do?cntId={m.group(1)}"
                            )

            if not final_url:
                continue
            if any(bad in final_url for bad in EXCLUDE_URL_KEYWORDS):
                continue
            if final_url in targets:
                continue

            img = link.find('img')
            if not img:
                try:
                    img = link.find_parent().find('img')
                except Exception:
                    pass

            if not img or not (img.get('src') or img.get('data-src')):
                skipped_no_thumb += 1
                continue

            thumb = urljoin(base_url, img.get('src') or img.get('data-src'))

            if not list_title_text:
                list_title_text = link.get_text(strip=True)
            if not list_title_text and img.get('alt'):
                list_title_text = img.get('alt').strip()

            targets[final_url] = {'thumb': thumb, 'list_title': list_title_text}

    except Exception as e:
        print(f"   ⚠️ 목록 수집 에러 [{site_name}]: {e}")

    if skipped_no_thumb > 0:
        print(f"   💡 썸네일 없는 글 {skipped_no_thumb}건 제외")

    return targets


# =========================================================
# 상세 페이지 방문 + 동적 콘텐츠 추출
# =========================================================
def visit_detail_pages(driver, targets: dict, site_name: str) -> dict:
    final_data = {}

    title_selectors_common = [
        ".event_view_top h3", "dl.event_view_tit > dt",
        ".eventView > dl > dt", ".title-wrap > h2.tit",
        ".c-board__title", ".view-tit", "h2", ".tit", ".subject"
    ]
    title_selectors_sk7 = ["strong.title"] + title_selectors_common
    bad_list = SK7_BAD_TITLES if site_name == "SK 7세븐모바일" else BAD_TITLES
    selectors = title_selectors_sk7 if site_name == "SK 7세븐모바일" else title_selectors_common

    for url, info in targets.items():
        try:
            driver.get(url)

            content_data = extract_page_content(driver, url)

            # ── 제목 추출 ──────────────────────
            title = ""
            if site_name == "SKT 다이렉트":
                title = info['list_title']
            else:
                for sel in selectors:
                    try:
                        for el in driver.find_elements(By.CSS_SELECTOR, sel):
                            txt = el.text.strip()
                            if txt and not any(b in txt for b in bad_list):
                                title = txt
                                break
                        if title:
                            break
                    except Exception:
                        pass

                if not title or any(b in title for b in bad_list):
                    lt = info['list_title'].strip()
                    if lt and not any(b in lt for b in bad_list):
                        title = lt

                if not title or any(b in title for b in bad_list):
                    title = driver.title.strip()

            # D-day / 취소선 / 날짜범위 정제
            title = clean_title(title)

            if not title or any(bad in title for bad in EXCLUDE_TITLE_KEYWORDS):
                continue
            if any(b in title for b in bad_list):
                continue
            if not info['thumb'] or not info['thumb'].strip():
                continue

            final_data[url] = {
                "title":        title,
                "img":          info['thumb'],
                "main_content": content_data["main_content"],
                "notice":       content_data["notice"],
                "full_text":    content_data["full_text"],
            }

            print(f"   ✓ [{site_name}] {title[:30]} | 본문 {len(content_data['main_content'])}자 | 유의사항 {len(content_data['notice'])}자")

        except Exception as e:
            print(f"   ⚠️ 상세 수집 실패: {url[:60]} | {e}")
            continue

    return final_data


# =========================================================
# 스카이라이프 전용 수집
# =========================================================
def _extract_skylife_detail_content(driver):
    """스카이라이프 상세페이지 아코디언 클릭 및 텍스트 추출"""
    notice_parts = []

    try:
        accordion_btns = driver.find_elements(
            By.CSS_SELECTOR, 'button[data-radix-collection-item][data-state="closed"]'
        )
        for btn in accordion_btns:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                time.sleep(0.3)
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(1.0)
            except Exception:
                continue

        xpath_btns = driver.find_elements(
            By.XPATH, '//button[(contains(text(),"안내") or contains(text(),"유의사항")) and @data-state="closed"]'
        )
        for btn in xpath_btns:
            try:
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(1.0)
            except Exception:
                pass

        open_regions = driver.find_elements(By.CSS_SELECTOR, 'div[role="region"][data-state="open"]')
        for region in open_regions:
            text = region.text.strip()
            if text and text not in notice_parts:
                notice_parts.append(text)

        nested_btns = driver.find_elements(
            By.CSS_SELECTOR, 'div[role="region"][data-state="open"] button[data-state="closed"]'
        )
        for btn in nested_btns:
            try:
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(0.7)
                new_regions = driver.find_elements(By.CSS_SELECTOR, 'div[role="region"][data-state="open"]')
                for r in new_regions:
                    t = r.text.strip()
                    if t and t not in notice_parts:
                        notice_parts.append(t)
            except Exception:
                pass

    except Exception as e:
        print(f"       ⚠️ 아코디언 처리 실패: {e}")

    main_content = ""
    try:
        iframes = driver.find_elements(By.CSS_SELECTOR, 'iframe#iframe-promotion')
        if iframes:
            srcdoc = iframes[0].get_attribute('srcdoc')
            if srcdoc:
                soup = BeautifulSoup(srcdoc, 'html.parser')
                main_content = soup.get_text(separator=' ', strip=True)
    except Exception:
        pass

    if not main_content:
        try:
            main_content = driver.find_element(By.TAG_NAME, 'main').text[:2000]
        except Exception:
            pass

    notice_text = ' | '.join(filter(None, notice_parts))
    return main_content, notice_text


def crawl_skylife(driver) -> dict:
    """스카이라이프 — 링크 기반 수집 + 아코디언 콘텐츠 추출"""
    print("   [스카이라이프] 링크 기반 수집 방식...")
    base_url = "https://www.skylife.co.kr"
    result = {}

    for page in range(1, 6):
        url = f"{base_url}/event?category=mobile&p={page}"
        try:
            driver.get(url)
            time.sleep(2)
        except Exception as e:
            print(f"   ⚠️ 페이지 로드 실패 (p{page}): {e}")
            break

        soup = BeautifulSoup(driver.page_source, 'html.parser')
        page_links = []

        for a in soup.find_all('a', href=True):
            href = a['href']
            if '/event/' not in href:
                continue
            if href.rstrip('/') in ('/event', '/event?category=mobile'):
                continue
            full_url = urljoin(base_url, href.split('?')[0])
            if full_url in result:
                continue

            img = a.find('img')
            if not img or not (img.get('src') or img.get('data-src')):
                continue
            thumb = urljoin(base_url, img.get('src') or img.get('data-src'))

            title_el = a.find(class_=re.compile(r'title|tit|heading', re.I))
            title_text = title_el.get_text(strip=True) if title_el else a.get_text(strip=True)
            page_links.append((full_url, thumb, title_text))

        if not page_links:
            print(f"   [스카이라이프] p{page} 링크 없음 — 수집 종료")
            break

        for detail_url, thumb, list_title in page_links:
            if detail_url in result:
                continue
            try:
                driver.get(detail_url)
                time.sleep(2)

                title = ""
                for sel in ["h1", "h2", ".title", ".tit"]:
                    try:
                        els = driver.find_elements(By.CSS_SELECTOR, sel)
                        for el in els:
                            t = el.text.strip()
                            if t and not any(b in t for b in BAD_TITLES):
                                title = t
                                break
                    except Exception:
                        pass
                    if title:
                        break
                if not title:
                    title = list_title or driver.title.strip()

                title = clean_title(title)

                if not title or any(b in title for b in EXCLUDE_TITLE_KEYWORDS):
                    continue

                main_content, notice = _extract_skylife_detail_content(driver)
                full_text = f"{main_content}\n\n{notice}".strip()

                result[detail_url] = {
                    "title":        title,
                    "img":          thumb,
                    "main_content": main_content[:50000],
                    "notice":       notice[:50000],
                    "full_text":    full_text[:50000],
                }
                print(f"   ✓ [스카이라이프] {title[:30]} | 본문 {len(main_content)}자 | 유의사항 {len(notice)}자")

            except Exception as e:
                print(f"   ⚠️ 상세 실패 ({detail_url[-40:]}): {e}")
                continue

    return result


# =========================================================
# 사이트별 로직 라우팅
# =========================================================
def crawl_site_logic(driver, comp: dict) -> dict:
    print(f"▶ {comp['name']} 수집 중...")

    if comp['name'] == "스카이라이프":
        return crawl_skylife(driver)

    site_cfg = {
        "U+ 유모바일":    {"keywords": ["event", "benefit"], "base": "https://www.uplusumobile.com"},
        "KTM 모바일":     {"keywords": ["eventDetail"],       "base": "https://www.ktmmobile.com"},
        "헬로모바일":     {"keywords": ["event"], "onclick": r"(\d+)", "base": "https://direct.lghellovision.net"},
        "SK 7세븐모바일": {"keywords": ["event", "main.do"], "onclick": r"['\"](\w+)['\"]", "base": "https://www.sk7mobile.com"},
        "SKT 다이렉트":   {"keywords": ["event", "plan"],    "base": "https://shop.tworld.co.kr"},
    }
    cfg = site_cfg.get(comp['name'], {"keywords": [], "base": ""})

    all_targets = {}
    for page in range(1, 11):
        if comp['param']:
            sep = "&" if "?" in comp['url'] else "?"
            t_url = f"{comp['url']}{sep}{comp['param']}={page}"
        else:
            t_url = comp['url']

        try:
            driver.get(t_url)
        except Exception as e:
            print(f"   ⚠️ 페이지 로드 실패 [{comp['name']} p{page}]: {e}")
            break

        page_targets = extract_list_with_thumbnails(
            driver, comp['name'],
            cfg.get('keywords', []),
            cfg.get('onclick'),
            cfg['base'],
            comp.get('selector')
        )

        if not page_targets:
            break
        all_targets.update(page_targets)
        if not comp['param']:
            break

    collected = visit_detail_pages(driver, all_targets, comp['name'])
    return collected


# =========================================================
# Main
# =========================================================
def main():
    competitors = [
        {"name": "SKT 다이렉트",   "url": "https://shop.tworld.co.kr/exhibition/submain",                            "param": None, "selector": "#wrap > div.container"},
        {"name": "KTM 모바일",     "url": "https://www.ktmmobile.com/event/eventBoardList.do",                       "param": None, "selector": ""},
        {"name": "U+ 유모바일",    "url": "https://www.uplusumobile.com/event-benefit/event/ongoing",                "param": None, "selector": ""},
        {"name": "스카이라이프",   "url": "https://www.skylife.co.kr/event?category=mobile",                         "param": "p",  "selector": ""},
        {"name": "헬로모바일",     "url": "https://direct.lghellovision.net/event/viewEventList.do?returnTab=allli", "param": None, "selector": ""},
        {"name": "SK 7세븐모바일", "url": "https://www.sk7mobile.com/bnef/event/eventIngList.do",                    "param": None, "selector": ""},
    ]

    results = {}
    driver = None
    start_time = time.time()

    print(f"🚀 크롤링 시작 [{NOW.strftime('%Y-%m-%d %H:%M KST')}]")
    print(f"📌 본문 + 유의사항 동적 추출 활성화 (스카이라이프 아코디언 파싱 적용)\n")

    try:
        driver = setup_driver()

        for comp in competitors:
            comp_start = time.time()
            try:
                data = crawl_site_logic(driver, comp)
                results[comp['name']] = data
                elapsed = time.time() - comp_start
                print(f"✅ {comp['name']} 완료 ({len(data)}건, {elapsed:.0f}초)")
            except Exception as e:
                print(f"⚠️ {comp['name']} 에러, 드라이버 재시작... [{type(e).__name__}: {e}]")
                try:
                    driver.quit()
                except Exception:
                    pass
                try:
                    driver = setup_driver()
                except Exception as de:
                    print(f"❌ 드라이버 재시작 실패: {de}")
                results[comp['name']] = {}

    except Exception as e:
        print(f"❌ 치명적 에러: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    output_path = os.path.join(DATA_DIR, f"data_{FILE_TIMESTAMP}.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    total_count = sum(len(v) for v in results.values())
    elapsed_total = time.time() - start_time

    print(f"\n{'='*60}")
    print(f"🎉 완료! 총 {total_count}건 | {elapsed_total:.0f}초 소요")
    print(f"💾 저장: {output_path}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
