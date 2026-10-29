"""
content_extractor.py
동적 본문 + 유의사항 추출 모듈 (아코디언 특화)

[수정] remove_noise_elements: decompose 후 el.attrs가 None이 되는 BS4 버그 방어
      → el.get() 호출 전 el.attrs is None 체크 추가
"""

import re
import time
from bs4 import BeautifulSoup, NavigableString, Comment
from promotion_noise import stable_text, COUNTER_SELECTORS
from notice_regions import extract_regions, region_spec, region_text
from campaign_content import extract_campaign_body
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    TimeoutException,
    StaleElementReferenceException,
)

NOISE_SELECTORS = [
    "header", "footer", "nav", "aside",
    ".gnb", ".lnb", ".snb", ".tnb",
    ".breadcrumb", ".sitemap",
    ".scroll-top", ".btn-top",
    ".share", ".sns-share",
    ".loading", ".spinner",
    ".cookie", ".popup", ".modal-backdrop",
    "script", "style", "noscript", "iframe",
]

NOISE_TEXT_PATTERNS = [
    r'D[-\s]?\d+',
    r'조회\s*\d+',
    r'좋아요\s*\d+',
    r'^(이전|다음|목록|닫기|열기|TOP|top)$',
    r'^(주메뉴|본문 바로가기|메뉴|바로가기)$',
    r'^\s*$',
]

NOTICE_KEYWORDS = [
    "유의사항", "주의사항", "유의 사항", "주의 사항",
    "안내사항", "참고사항", "이용안내", "약관",
    "주의", "안내", "notice", "caution", "guide",
]

NOTICE_SELECTORS = [
    ".caution", ".notice", ".guide", ".terms",
    ".event-caution", ".event-notice", ".event-guide",
    ".info-box", ".info-wrap", ".info-area",
    ".tip", ".tip-box",
    ".precaution", ".attention",
    "[class*='caution']", "[class*='notice']",
    "[class*='guide']", "[class*='terms']",
    "[class*='info']",
    "#caution", "#notice", "#guide",
    ".event-detail-notice", ".event_caution",
    ".cont-caution", ".cont-notice",
    ".view-caution", ".event-view-caution",
]

MAIN_CONTENT_SELECTORS = [
    ".event-detail", ".event_detail",
    ".event-view", ".event_view",
    ".event-content", ".event_content",
    ".view-content", ".view_content",
    ".cont-wrap", ".cont_wrap",
    ".board-view", ".board_view",
    ".detail-wrap", ".detail_wrap",
    "article", "main",
    "#content", "#contents",
    ".content", ".contents",
]


# In SK7 event pages, user-submitted reviews and comment lists may live
# alongside the campaign. Keep the campaign's "후기 작성" benefit/terms.
SK7_USER_REVIEW_SELECTORS = (
    '.review-list, .review_list, .reviewList, .review-items, .reviewItems, '
    '.review-item, .review_item, .reviewItem, .user-review, .userReview, '
    '.review-comments, .reviewComments, .comment-list, .comment_list, '
    '.commentList, .comment-item, .reply-list, .reply_list, .replyList, '
    '[id*="reviewList" i], [id*="commentList" i], [id*="replyList" i]'
)


def remove_sk7_user_reviews(soup: BeautifulSoup) -> None:
    for item in soup.select(SK7_USER_REVIEW_SELECTORS):
        item.decompose()


# =========================================================
# 아코디언 클릭 (4가지 방법 순차 시도)
# =========================================================
def _is_inline_expander(el):
    """Only expand content in place; never follow a promotion/popup link."""
    try:
        href = (el.get_attribute('href') or '').strip().lower()
        if href and not href.startswith('#') and not re.fullmatch(r'javascript:\s*(?:void\(0\)|;?)\s*;?', href):
            return False
        if (el.get_attribute('target') or '').lower() == '_blank':
            return False
        onclick = (el.get_attribute('onclick') or '').lower()
        if re.search(r'window\.open|location[.=]|(?:open|show)\w*(?:popup|layer)|(?:popup|layer)\w*(?:open|show)', onclick):
            return False
        toggle = (el.get_attribute('data-toggle') or '').lower()
        if toggle in ('modal', 'dialog', 'popover'):
            return False
        return True
    except StaleElementReferenceException:
        return False


def _safe_click(driver, el) -> bool:
    if el.get_attribute('aria-expanded') == 'true':
        return False
    # SK7 puts expanded state on the sibling dd, not the button. Its
    # already-open button is labelled '닫기'; clicking it closes valid terms.
    if (el.get_attribute('title') or '').strip() == '닫기':
        return False
    if el.tag_name in ('div', 'section', 'dl'):
        # data-accordion containers are not toggles. Clicking their centre
        # depends on layout and can hit an arbitrary descendant button.
        return False
    try:
        panel = el.find_element(By.XPATH, "ancestor::dt[1]/following-sibling::dd[1]")
        if panel.get_attribute('aria-expanded') == 'true':
            return False
    except Exception:
        pass
    if not _is_inline_expander(el):
        return False
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center', behavior:'smooth'});", el
        )
        time.sleep(0.25)

        try:
            parent = el.find_element(By.XPATH, "..")
            before_h = driver.execute_script("return arguments[0].scrollHeight;", parent)
        except Exception:
            before_h = 0

        try:
            el.click()
        except (ElementClickInterceptedException, ElementNotInteractableException):
            driver.execute_script("arguments[0].click();", el)

        time.sleep(0.45)

        try:
            after_h = driver.execute_script("return arguments[0].scrollHeight;", parent)
            return after_h > before_h
        except Exception:
            return True

    except StaleElementReferenceException:
        return False
    except Exception:
        return False


def _click_accordions(driver) -> int:
    clicked = 0
    seen = set()

    def expand_once(el):
        key = el.id
        if key in seen:
            return False
        seen.add(key)
        return _safe_click(driver, el)

    # A: aria-expanded="false"
    try:
        for el in driver.find_elements(By.CSS_SELECTOR, "[aria-expanded='false']"):
            if not el.is_displayed():
                continue
            try:
                label = (
                    el.text.strip()
                    + (el.get_attribute("title") or "")
                    + (el.get_attribute("aria-label") or "")
                )
                el_class = el.get_attribute("class") or ""
            except Exception:
                continue
            is_accordion_class = any(
                c in el_class for c in ["accordion", "toggle", "collapse", "fold", "more"]
            )
            if any(kw in label for kw in NOTICE_KEYWORDS) or is_accordion_class:
                if expand_once(el):
                    clicked += 1
    except Exception:
        pass

    # B: data-* 커스텀 속성
    try:
        for el in driver.find_elements(
            By.CSS_SELECTOR, "[data-toggle], [data-open], [data-accordion], [data-collapse]"
        ):
            if not el.is_displayed():
                continue
            label = el.text + (el.get_attribute('aria-label') or '')
            if not (el.get_attribute('data-accordion') is not None
                    or el.get_attribute('data-collapse') is not None
                    or any(keyword in label for keyword in NOTICE_KEYWORDS)):
                continue
            try:
                parent_class = el.find_element(By.XPATH, "..").get_attribute("class") or ""
            except Exception:
                parent_class = ""
            if any(c in parent_class for c in ["open", "active", "expanded", "show"]):
                continue
            if expand_once(el):
                clicked += 1
    except Exception:
        pass

    # C: <details>/<summary> HTML5
    try:
        for el in driver.find_elements(By.TAG_NAME, "summary"):
            if not el.is_displayed():
                continue
            try:
                parent_details = el.find_element(By.XPATH, "..")
                if parent_details.get_attribute("open") is not None:
                    continue
            except Exception:
                pass
            text = el.text.strip()
            if any(kw in text for kw in NOTICE_KEYWORDS) or not text:
                if expand_once(el):
                    clicked += 1
    except Exception:
        pass

    # D: 버튼 텍스트에 유의사항 키워드
    try:
        for el in driver.find_elements(
            By.CSS_SELECTOR,
            "button, a[role='button'], a[role='tab'], li[role='tab'], .tab-item, .tab-btn"
        ):
            if not el.is_displayed():
                continue
            try:
                text = el.text.strip()
                el_class = el.get_attribute("class") or ""
            except Exception:
                continue
            if not any(kw in text for kw in NOTICE_KEYWORDS):
                continue
            if len(text) > 20:
                continue
            if any(c in el_class for c in ["active", "on", "selected", "current"]):
                continue
            if expand_once(el):
                clicked += 1
    except Exception:
        pass

    return clicked


# =========================================================
# 페이지 로딩 + 스크롤
# =========================================================
def _scroll_page(driver, step_ratio: float = 0.8):
    try:
        viewport_h = driver.execute_script("return window.innerHeight") or 800
        step = max(int(viewport_h * step_ratio), 300)
        current = 0
        for _ in range(40):
            total_h = driver.execute_script("return document.body.scrollHeight") or 0
            if current >= total_h:
                break
            driver.execute_script(f"window.scrollTo(0, {current});")
            time.sleep(0.15)
            current += step
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.3)
    except Exception:
        pass


def wait_and_expand(driver, timeout: int = 8) -> int:
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except TimeoutException:
        pass

    _scroll_page(driver)
    clicked = _click_accordions(driver)

    if clicked > 0:
        time.sleep(0.4)
        _scroll_page(driver, step_ratio=0.5)

    return clicked


# =========================================================
# HTML 노이즈 제거
# =========================================================
def remove_noise_elements(soup: BeautifulSoup, preserve_collapsed=False) -> BeautifulSoup:
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    for el in soup.select(COUNTER_SELECTORS + ', .evt-strip'):
        el.decompose()
    for el in list(soup.find_all(True)):
        try:
            # ★ 핵심 수정: decompose된 요소는 attrs가 None → 건너뜀
            if el.attrs is None:
                continue

            style = el.get('style', '').replace(' ', '')
            classes = ' '.join(el.get('class', []))

            if not preserve_collapsed and (
                'display:none' in style
                or 'visibility:hidden' in style
                or el.get('hidden') is not None
                or el.get('aria-hidden') == 'true'
                or any(c in classes for c in ['hidden', 'blind', 'sr-only', 'a11y', 'skip'])
            ):
                el.decompose()
        except Exception:
            continue  # 어떤 이유로든 실패하면 그냥 넘어감

    for sel in NOISE_SELECTORS:
        try:
            for el in soup.select(sel):
                el.decompose()
        except Exception:
            pass

    return soup


def is_noise_text(text: str) -> bool:
    text = text.strip()
    if len(text) < 2:
        return True
    return any(re.fullmatch(p, text) for p in NOISE_TEXT_PATTERNS)


def extract_clean_text(element) -> str:
    if element is None:
        return ""
    lines, seen = [], set()
    try:
        for item in element.descendants:
            if isinstance(item, NavigableString):
                text = str(item).strip()
                if text and not is_noise_text(text) and text not in seen:
                    seen.add(text)
                    lines.append(text)
    except Exception:
        pass
    return stable_text('\n'.join(lines))


# =========================================================
# 유의사항 추출
# =========================================================
def _merge(texts: list) -> str:
    lines, seen = [], set()
    for text in texts:
        for line in text.split('\n'):
            line = line.strip()
            if line and line not in seen and not is_noise_text(line):
                seen.add(line)
                lines.append(line)
    return '\n'.join(lines)


def extract_notice_section(soup: BeautifulSoup) -> str:
    results = []

    # 1순위: CSS 선택자 직접 매칭
    for sel in NOTICE_SELECTORS:
        try:
            for el in soup.select(sel):
                text = extract_clean_text(el)
                if len(text) > 20:
                    results.append(text)
        except Exception:
            pass
    if results:
        return _merge(results)

    # 2순위: 제목 태그에 유의사항 키워드
    for tag in soup.find_all(['h1','h2','h3','h4','h5','h6','dt','strong','b','p']):
        try:
            if any(kw in tag.get_text(strip=True) for kw in NOTICE_KEYWORDS):
                section = tag.find_parent(['div', 'section', 'article', 'dl'])
                if section:
                    text = extract_clean_text(section)
                    if len(text) > 20:
                        results.append(text)
                else:
                    sibs = [
                        s.get_text(strip=True)
                        for s in tag.next_siblings
                        if hasattr(s, 'get_text')
                    ]
                    if sibs:
                        results.append('\n'.join(sibs[:30]))
        except Exception:
            continue
    if results:
        return _merge(results)

    # 3순위: 조건 키워드 밀집 리스트
    COND_KWS = ['원', '%', '요금제', '기간', '조건', '해지', '위약금', '자동', '만료', '적용']
    best, best_score = None, 0
    for parent in soup.find_all(['ul', 'ol', 'dl']):
        try:
            items = parent.find_all(['li', 'dd'])
            if len(items) < 2:
                continue
            score = sum(1 for i in items if any(k in i.get_text() for k in COND_KWS))
            if score > best_score:
                best_score, best = score, parent
        except Exception:
            continue
    if best and best_score >= 2:
        text = extract_clean_text(best)
        if text:
            results.append(text)

    return _merge(results) if results else ""


# =========================================================
# 본문 추출
# =========================================================
def extract_main_content(soup: BeautifulSoup) -> str:
    for sel in NOTICE_SELECTORS:
        try:
            for el in soup.select(sel):
                el.decompose()
        except Exception:
            pass

    for sel in MAIN_CONTENT_SELECTORS:
        try:
            el = soup.select_one(sel)
            if el:
                text = extract_clean_text(el)
                if len(text) > 50:
                    return text
        except Exception:
            pass

    best, best_len = None, 0
    for div in soup.find_all('div'):
        try:
            t = div.get_text(strip=True)
            if best_len < len(t) < 10000:
                best_len, best = len(t), div
        except Exception:
            continue
    return extract_clean_text(best) if best else ""


# =========================================================
# 외부 API
# =========================================================
def extract_page_content(driver, url: str) -> dict:
    empty = {"main_content": "", "notice": "", "full_text": "", "accordion_clicked": 0}
    try:
        # Known sites embed the complete panel text in the DOM. Do not toggle
        # those panels: CSS/ARIA state differs by site and is not completeness.
        regions = None
        if region_spec(url):
            last, stable = None, 0
            def regions_ready(d):
                nonlocal last, stable, regions
                regions = extract_regions(d.page_source, url, region_text)
                fingerprint = repr(regions.get('_notice_regions'))
                stable = stable + 1 if fingerprint == last else 0
                last = fingerprint
                return regions['_notice_complete'] and stable >= 2
            try:
                WebDriverWait(driver, 8, poll_frequency=0.4).until(regions_ready)
            except TimeoutException:
                # Preserve partial observations for diagnosis; never call them complete.
                if regions is not None:
                    regions['_notice_complete'] = False
            acc_clicked = 0
        else:
            acc_clicked = wait_and_expand(driver)

        scoped_body = extract_campaign_body(driver.page_source, url)
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        notice_scope = ''
        is_sk7 = 'www.sk7mobile.com/' in url
        if is_sk7:
            detail = soup.select_one('.bbs-view')
            if detail is not None:
                soup = BeautifulSoup(str(detail), 'html.parser')
                remove_sk7_user_reviews(soup)
                notice_scope = '.bbs-view'
        else:
            for selector in MAIN_CONTENT_SELECTORS:
                detail = soup.select_one(selector)
                if detail is not None:
                    soup = BeautifulSoup(str(detail), 'html.parser')
                    notice_scope = selector
                    break
        # Collapsed terms remain valid content even when their panel is hidden.
        notice_soup = remove_noise_elements(BeautifulSoup(str(soup), 'html.parser'), preserve_collapsed=True)
        # Dialogs and unused templates are not collapsed campaign terms.
        for el in notice_soup.select('template, [role="dialog"], [aria-modal="true"]'):
            el.decompose()
        notice = extract_notice_section(notice_soup)
        missing_panels = False
        for control in notice_soup.select('[aria-controls]'):
            if not any(word in control.get_text(' ', strip=True) for word in NOTICE_KEYWORDS):
                continue
            for target in control.get('aria-controls', '').split():
                panel = notice_soup.find(id=target)
                if panel is None or not panel.get_text(strip=True):
                    missing_panels = True
        soup = remove_noise_elements(soup)
        campaign = soup.select_one('.view-body .eventCont') if is_sk7 else None
        image_campaign = bool(campaign is not None and campaign.select_one("img[src], img[data-src]"))
        if campaign is not None:
            # A SK7 promotion may have a separate user-review feed in .bbs-view.
            # Only campaign copy belongs in the event body; never fall back to reviews.
            campaign = BeautifulSoup(str(campaign), 'html.parser')
            for sel in NOTICE_SELECTORS:
                for el in campaign.select(sel):
                    el.decompose()
            main_content = extract_clean_text(campaign)
        else:
            main_content = extract_main_content(soup)

        parts = []
        if main_content:
            parts.append(f"[본문]\n{main_content}")
        if notice:
            parts.append(f"[유의사항]\n{notice}")
        full_text = '\n\n'.join(parts)

        result = {
            '_notice_scope': notice_scope,
            # Empty or unknown-scope observations require review, never deletion.
            '_notice_complete': bool(notice_scope and notice and not missing_panels and not notice_soup.select('[aria-busy="true"], .loading, .spinner')),
            "_body_status":      "image_only" if image_campaign and not main_content else ("text" if main_content else "unavailable"),
            "main_content":      main_content,
            "notice":            notice,
            "full_text":         full_text,
            "accordion_clicked": acc_clicked,
        }
        if regions is not None:
            result.update(regions)
            result['full_text'] = '\n\n'.join(filter(None, [main_content, regions['notice']]))
        if scoped_body is not None:
            result.update(scoped_body)
            result['full_text'] = '\n\n'.join(filter(None, [result['main_content'], result['notice']]))
        return result

    except Exception as e:
        print(f"   ⚠️ 콘텐츠 추출 실패 [{url[:50]}]: {e}")
        raise RuntimeError(f"콘텐츠 추출 실패: {url}") from e



