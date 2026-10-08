"""
parser.py
[구조]
- 테이블 1: dashboard_latest.csv / dashboard_history.csv
  → 이벤트 수집본 저장 (AI 없음), notice 필드는 정제된 순수 텍스트

- 테이블 2: notices_latest.csv / notices_history.csv
  → 회사별 유의사항 이벤트 단위 중복 제거해서 수집 (금액 등 AI 추출 없음)
  → 컬럼: company, notice_text, benefit_amt, benefit_type, cond_type, cond_plan_price, summary
    (benefit_amt 등은 항상 기본값 — 과거 이력과의 컬럼 호환을 위해 유지)

[수정 내역]
- clean_html_to_text(): HTML 태그/주석 완전 제거 → notice 저장 전 항상 적용
- FOOTER_NOISE_KEYWORDS + is_footer_noise(): 푸터/네비 노이즈 감지
- collect_unique_notices(): HTML 제거 → 노이즈 필터 → fingerprint 순서로 수정
- event_rows 저장 시 notice 필드도 clean_html_to_text() 적용
"""

import os
from monitor_core import (detect_changes, calculate_notice_diff, validate_snapshot,
                          collection_warnings, notice_lines, comparison_snapshots, snapshot_warnings)
import json
import time
import glob
import re
import hashlib
import requests
import pandas as pd
from bs4 import BeautifulSoup, Comment
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

load_dotenv()
slack_webhook_url = os.getenv("SLACK_WEBHOOK_URL")

KST = timezone(timedelta(hours=9))

EVENT_COLUMNS = [
    "date", "company", "title", "url", "image", "category",
]

NOTICE_COLUMNS = [
    "date", "company", "notice_text", "url", "title",
    "benefit_amt", "benefit_type", "cond_type", "cond_plan_price", "summary",
]

# =========================================================
# 푸터/네비 노이즈 감지
# (content_extractor.py와 동일한 기준 — import 없이 독립 유지)
# =========================================================

FOOTER_NOISE_KEYWORDS = [
    '이용약관', '개인정보 처리방침', '개인정보처리방침',
    '이메일 무단 수집거부', '분쟁처리절차', '프라이버시 센터',
    '이용내역', '이용 내역',
    '유심구매하기', '다이렉트몰 구매하기', '오픈마켓 구매하기',
    '편의점/마트 구매하기',
    '요금제 소개', '요금제 비교', '전체 부가서비스',
    '스마트폰 비교', '로그인', '회원가입', '마이페이지',
    '자주 묻는 질문', '1:1 문의',
    '없다면?', '있다면?', 'eSIM', '워치',
]

HTML_ARTIFACT_PATTERNS = [
    r'^공통\s*::\s*(START|END)$',
    r'^콘텐츠영역\s*(START|END)$',
    r'^(START|END)\s*$',
    r'^<!--.*-->$',
    r'^//\s*',
]

# 유의사항에서 제거할 타임스탬프/날짜 전용 줄 패턴
# (페이지 수정일, 크롤링 시각 등이 notice에 섞이는 경우 방어)
TIMESTAMP_LINE_PATTERNS = [
    r'^\d{4}[-./]\d{2}[-./]\d{2}\s*\d{2}:\d{2}(:\d{2})?$',   # 2026-03-09 14:25
    r'^\d{4}년\s*\d{1,2}월\s*\d{1,2}일$',                     # 2026년 03월 09일
    r'^\d{4}[-./]\d{2}[-./]\d{2}$',                            # 2026-03-09
    r'^\d{2}:\d{2}(:\d{2})?$',                                 # 14:25
]

def is_timestamp_line(line: str) -> bool:
    """페이지 수정일/크롤링 시각 등 날짜/시간 전용 줄 감지"""
    return any(re.fullmatch(p, line.strip()) for p in TIMESTAMP_LINE_PATTERNS)


def clean_html_to_text(html_or_text: str) -> str:
    if not html_or_text:
        return ""
    text = str(html_or_text)
    if '<' in text and '>' in text:
        soup = BeautifulSoup(text, 'html.parser')
        for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
            comment.extract()
        for tag in soup(['script', 'style', 'noscript']):
            tag.decompose()
        text = soup.get_text(separator='\n', strip=True)
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    lines = []
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        if any(re.fullmatch(p, line) for p in HTML_ARTIFACT_PATTERNS):
            continue
        lines.append(line)
    text = '\n'.join(lines)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()


def is_footer_noise(text: str) -> bool:
    if not text:
        return False
    matched = sum(1 for kw in FOOTER_NOISE_KEYWORDS if kw in text)
    return matched >= 3


# =========================================================
# 유틸
# =========================================================

def classify_category(title: str) -> str:
    if any(x in title for x in ["친구", "추천", "초대"]): return "친구추천"
    if any(x in title for x in ["요금제", "데이터", "무제한"]): return "요금제"
    if any(x in title for x in ["가입", "개통", "신규", "유심"]): return "가입혜택"
    if any(x in title for x in ["리뷰", "후기"]): return "리뷰이벤트"
    return "기타"


def text_fingerprint(text: str) -> str:
    normalized = re.sub(r'\s+', ' ', (text or "").strip())
    return hashlib.md5(normalized.encode('utf-8')).hexdigest()[:8]


def normalize_for_dedup(text: str) -> str:
    """Whitespace only: amounts, dates and quantities are material conditions."""
    return re.sub(r'\s+', ' ', text).strip()


# ── 푸터 줄: 줄 단위 제거 (블록 전체 날리지 않음) ──────────────────────
FOOTER_LINE_EXACT = {
    '이용약관', '개인정보 처리방침', '이메일 무단 수집거부', '분쟁처리절차',
    '온라인 제휴', '이용자 피해예방', '프라이버시 센터', '개인정보 이용내역',
    '유심구매하기', '다이렉트몰 구매하기', '요금제 소개', '요금제 비교',
    '없다면?', '있다면?', 'eSIM 개통 안내', '워치 개통 안내',
    'T direct shop 이용약관', '운영 정책 및 약관',
}
FOOTER_LINE_STARTS = (
    '이용약관', '개인정보', '이메일 무단', '분쟁처리', '온라인 제휴',
    '이용자 피해', '프라이버시', 'T direct shop',
)

def is_footer_line(line: str) -> bool:
    s = line.strip()
    return s in FOOTER_LINE_EXACT or s.startswith(FOOTER_LINE_STARTS)


# ── 섹션 헤더 줄: 내용 없는 제목 줄 ─────────────────────────────────────
SECTION_HEADER_RE = re.compile(
    r'^(?:'
    r'.*유의\s*사항$|.*안내$|.*주의$|.*확인$|.*사항$|'  # "~유의사항", "개통 안내" 등
    r'이벤트 공통사항|공통 유의|운영 정책|약관|꼭 확인'
    r')$'
)

def is_section_header(line: str) -> bool:
    s = line.strip()
    # 짧고(20자 이하) 패턴 일치 → 섹션 제목으로 판단
    return len(s) <= 25 and bool(SECTION_HEADER_RE.match(s))


# ── 항목 새로 시작 마커 ────────────────────────────────────────────────
ITEM_NEW_RE = re.compile(
    r'^(?:'
    r'[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮]|'    # 원문자
    r'\d{1,2}[\.\)]\s|'                   # 1.  1)
    r'\(\d{1,2}\)\s|'                      # (1)
    r'[가-하]\.\s'                          # 가.  나.
    r')'
)

# ── 하위 항목 마커 (이전 항목에 붙임) ─────────────────────────────────
SUB_ITEM_RE = re.compile(r'^[ㄴ└↳\-\*·•ㆍ]\s')

def split_into_notice_items(text: str) -> list:
    """
    줄 단위로 쪼갠 뒤, 하위 항목·연속 줄을 올바르게 묶어 '유의사항 1개 단위' 반환.

    규칙:
    1. 푸터 줄(이용약관 등) → 건너뜀
    2. 섹션 헤더 줄("유의사항", "주의" 등 짧은 제목) → 건너뜀
    3. 하위 항목 마커(ㄴ, -, •, └ 등) → 이전 항목에 이어붙임
    4. 새 항목 마커(원문자, 1., (1) 등) → 새 항목 시작
    5. 나머지 줄 = 완결된 독립 항목 (각 줄이 이미 완전한 문장)
    """
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    items   = []   # 완성된 항목들
    current = []   # 현재 조립 중인 항목

    def flush():
        if current:
            merged = ' '.join(current)
            if len(merged) >= 15 or re.search(r'20\d{2}\s*(?:[./-]|년)\s*\d{1,2}\s*(?:[./-]|월)\s*\d{1,2}', merged):
                items.append(merged)
            current.clear()

    for line in lines:
        if is_footer_line(line):
            continue
        if is_section_header(line):
            continue

        if SUB_ITEM_RE.match(line):
            # 하위 항목 → 이전 항목에 붙이기
            if current:
                current.append(line)
            # 이전 항목 없으면 그냥 독립 항목 취급
            else:
                current.append(line)

        elif ITEM_NEW_RE.match(line):
            # 번호/원문자 → 새 항목 시작
            flush()
            current.append(line)

        else:
            # 일반 줄: 이전 current 에 내용이 있고 하위 줄이면 붙이고,
            # 그렇지 않으면 직전 항목 완결 후 새 독립 항목 시작
            if current and SUB_ITEM_RE.match(current[-1]):
                # 하위 항목들이 이어지는 중 → 계속 붙임
                current.append(line)
            else:
                flush()
                current.append(line)

    flush()
    return items


# =========================================================
# 회사별 유의사항 수집 + 중복 제거
# =========================================================

def collect_unique_notices(items: dict, company: str = "") -> list:
    """
    1. 회사의 모든 이벤트 notice를 전부 합침
    2. 항목 단위로 쪼갬 (하위 항목 병합, 섹션 헤더 제거)
    3. 푸터 줄 제거
    4. 이벤트 URL별 원문 중복 제거 → 같은 패턴의 숫자만 다른 변형 제거
    5. 유니크 항목 목록 반환
    """
    all_items = []

    for url, info in items.items():
        raw = (info.get('notice', '') or '').strip()
        if not raw:
            continue
        cleaned = clean_html_to_text(raw)
        if not cleaned:
            continue

        for item_text in split_into_notice_items(cleaned):
            # 금액 혜택/금액 조건이 없는 일반 안내, 조회수, 검색일 등은 저장하지 않는다.
            if notice_lines(item_text):
                all_items.append({"line": item_text, "url": url, "title": info.get('title', '')})

    # 이벤트 URL별 원문 중복 제거
    seen_normalized = set()
    seen_exact      = set()
    result = []
    skipped_dup = 0

    for item in all_items:
        # 회사 전체 유의사항을 하나로 합치므로 이벤트 URL이 달라도 같은 문장은 한 번만 남긴다.
        exact_fp = normalize_for_dedup(item["line"])
        norm_fp = exact_fp

        if exact_fp in seen_exact or norm_fp in seen_normalized:
            skipped_dup += 1
            continue

        seen_exact.add(exact_fp)
        seen_normalized.add(norm_fp)
        result.append({
            "url":    item["url"],
            "title":  item["title"],
            "notice": item["line"],
        })

    print(f"      📋 전체 {len(all_items)}항목 → 중복/유사 {skipped_dup}개 제거 → 유니크 {len(result)}항목")
    return result


# 금액 등 수치 추출 없이 항상 기본값으로 저장 (과거 이력과 컬럼 호환용)
_NOTICE_DEFAULT = {
    "benefit_amt": 0, "benefit_type": "NO_BENEFIT",
    "cond_type": "ETC", "cond_plan_price": 0, "summary": "",
}


# =========================================================
# 변경 감지
# =========================================================

def calculate_changes(current_data: dict, prev_data: dict) -> tuple:
    """Compact company-level event summary; preserve all per-event comparisons."""
    current_data, prev_data = comparison_snapshots(current_data, prev_data)
    changes, details = {}, []
    for company in sorted(set(current_data) | set(prev_data)):
        curr, prev = current_data.get(company, {}), prev_data.get(company, {})
        curr_urls, prev_urls = set(curr), set(prev)
        added_urls, removed_urls = sorted(curr_urls - prev_urls), sorted(prev_urls - curr_urls)
        modified = [(url, detect_changes(prev[url], curr[url]))
                    for url in sorted(curr_urls & prev_urls)]
        modified = [(url, delta) for url, delta in modified if delta]
        total = len(added_urls) + len(removed_urls) + len(modified)
        if not total:
            continue

        changes[company] = total
        parts = []
        if added_urls:
            parts.append(f"신규 {len(added_urls)}")
        if removed_urls:
            parts.append(f"종료 {len(removed_urls)}")
        if modified:
            parts.append(f"수정 {len(modified)}")
        summary = f"• {company}: {'·'.join(parts)}"

        # Show only one representative link, never a URL dump.
        if modified:
            url, delta = modified[0]
            labels = {'main_content': '본문', 'detail_image_hashes': '이미지',
                      'title': '제목', 'img': '썸네일'}
            kinds = '·'.join(labels[k] for k in labels if k in delta)
            title = re.sub(r'\s+', ' ', curr[url].get('title') or '이벤트')[:24]
            summary += f" | {title}({kinds}) <{url}|보기>"
        elif added_urls:
            summary += f" <{added_urls[0]}|보기>"
        details.append(summary)

    return sum(changes.values()), changes, details


def _get_notice_lines(data: dict, company: str) -> set:
    """
    JSON에서 회사의 모든 이벤트 notice를 수집,
    HTML 정제 + 푸터 노이즈 제거 후 줄 단위 집합으로 반환.
    각 줄은 의미있는 문장 단위 (10자 이상).
    """
    skip_footer = (company == "스카이라이프")
    lines = set()
    for info in data.get(company, {}).values():
        raw = (info.get('notice', '') or '').strip()
        if not raw:
            continue
        cleaned = clean_html_to_text(raw)
        if not cleaned:
            continue
        if not skip_footer and is_footer_noise(cleaned):
            continue
        for line in cleaned.split('\n'):
            line = line.strip()
            if line and len(line) >= 10 and not is_timestamp_line(line):
                lines.add(line)
    return lines


def calculate_notice_changes(curr_data: dict, prev_data: dict) -> dict:
    return calculate_notice_diff(curr_data, prev_data)


# =========================================================
# Slack
# =========================================================

SLACK_COMPANY_LIMIT = 4
SLACK_NOTICE_LIMIT = 3
NOTICE_DATE_TOKEN_RE = re.compile(
    r'20\d{2}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일|'
    r'20\d{2}[./-]\d{1,2}[./-]\d{1,2}|'
    r'\d{1,2}\s*월\s*\d{1,2}\s*일|'
    r'\d{1,2}\s*[./-]\s*\d{1,2}'
)
PERIOD_HINT_RE = re.compile(r'기간|기한|까지|부터|종료|시작|지급일|발송일|선정일|사용일|신청일')


def _short_notice_change(changes):
    """A date-only edit becomes a compact old→new summary."""
    added, removed = changes.get('added', []), changes.get('removed', [])
    old_sources = changes.get('removed_sources', {})
    new_sources = changes.get('added_sources', {})
    for new_line in added[:10]:
        new_tokens = NOTICE_DATE_TOKEN_RE.findall(new_line)
        if not new_tokens or not PERIOD_HINT_RE.search(new_line):
            continue
        for old_line in removed[:10]:
            old_tokens = NOTICE_DATE_TOKEN_RE.findall(old_line)
            if not old_tokens or not PERIOD_HINT_RE.search(old_line):
                continue
            old_url = old_sources.get(old_line, {}).get('url')
            new_url = new_sources.get(new_line, {}).get('url')
            if old_url and new_url and old_url != new_url:
                continue
            before = NOTICE_DATE_TOKEN_RE.sub('{date}', old_line)
            after = NOTICE_DATE_TOKEN_RE.sub('{date}', new_line)
            if before != after or old_tokens == new_tokens:
                continue
            pair = next(((a, b) for a, b in zip(old_tokens, new_tokens) if a != b), None)
            if pair:
                return '기간 ' + re.sub(r'\s+', '', pair[0])[:18] + '→' + re.sub(r'\s+', '', pair[1])[:18]
    if any(PERIOD_HINT_RE.search(line) for line in added + removed):
        return '기간·조건 변경'
    return '혜택·조건 변경'


def _notice_source_link(changes):
    for key in ('added', 'removed'):
        for line in changes.get(key, []):
            url = changes.get(f'{key}_sources', {}).get(line, {}).get('url')
            if url and url.startswith(('https://', 'http://')):
                return f' <{url}|보기>'
    return ''


def format_slack_report(total_change, event_details, notice_changes, warnings=None):
    """Summarize changes for mobile Slack, without sending the full diff."""
    now_str = datetime.now(KST).strftime('%m/%d %H:%M')
    dashboard_url = os.getenv(
        'DASHBOARD_URL',
        'https://share.streamlit.io/rodolfochoi911-lgtm/competitor-monitor/main/Home.py'
    )
    notice_added = sum(len(v.get('added', [])) for v in notice_changes.values())
    notice_removed = sum(len(v.get('removed', [])) for v in notice_changes.values())
    lines = [f'[{now_str}] 경쟁사 모니터']

    if not (total_change or notice_changes or warnings):
        lines.append('특이사항 없음')
    else:
        lines.append(f'이벤트 {total_change}건 · 유의사항 +{notice_added}/-{notice_removed}줄')
        lines += event_details[:SLACK_COMPANY_LIMIT]
        if len(event_details) > SLACK_COMPANY_LIMIT:
            lines.append(f'  외 {len(event_details) - SLACK_COMPANY_LIMIT}개 회사')
        for company, change in list(sorted(notice_changes.items()))[:SLACK_NOTICE_LIMIT]:
            count = f"+{len(change.get('added', []))}/-{len(change.get('removed', []))}"
            summary = _short_notice_change(change)
            lines.append(f'• {company} 유의 {count}: {summary}{_notice_source_link(change)}')
        if len(notice_changes) > SLACK_NOTICE_LIMIT:
            lines.append(f'  외 {len(notice_changes) - SLACK_NOTICE_LIMIT}개 회사 유의사항')
        if warnings:
            lines.append(
                f'⚠️ 수집 점검 {len(warnings)}건 '
                '<https://github.com/rodolfochoi911-lgtm/competitor-monitor/actions|로그>'
            )

    lines.append(f'<{dashboard_url}|대시보드>')
    return '\n'.join(lines)


def send_slack_report(total_change: int, event_details: list, notice_changes: dict, warnings=None):
    if not slack_webhook_url:
        print('⚠️ SLACK_WEBHOOK_URL 없음')
        return
    msg = format_slack_report(total_change, event_details, notice_changes, warnings)
    try:
        r = requests.post(slack_webhook_url, json={'text': msg}, timeout=10)
        r.raise_for_status()
        print('✅ Slack 발송 완료!')
    except Exception as e:
        raise RuntimeError('Slack 알림 전송 실패') from e


# =========================================================
# CSV 안전 저장
# =========================================================
# GitHub는 단일 파일이 100MB를 넘으면 push 자체를 거부한다(GH001).
# history 파일이 이 한도에 가까워지면 지금까지의 내용은 날짜가 찍힌
# 파일로 보관(archive)하고, 새 history 파일을 다시 처음부터 쌓는다.
# → 과거 데이터는 하나도 안 지워지고 그대로 저장소에 남아있음.
HISTORY_SIZE_LIMIT_BYTES = 90 * 1024 * 1024  # 90MB (100MB 한도에 여유 마진)


def _archive_if_too_large(history_path: str):
    if not os.path.exists(history_path):
        return
    if os.path.getsize(history_path) < HISTORY_SIZE_LIMIT_BYTES:
        return
    base, ext = os.path.splitext(history_path)
    archive_path = f"{base}_archive_{datetime.now().strftime('%Y%m%d')}{ext}"
    os.rename(history_path, archive_path)
    print(f"📦 {history_path} 가 90MB를 넘어서 {archive_path} 로 보관하고 새로 시작합니다.")


def safe_save(df_new: pd.DataFrame, latest_path: str, history_path: str, columns: list):
    for col in columns:
        if col not in df_new.columns:
            df_new[col] = ""
    df_new = df_new[columns]

    df_new.to_csv(latest_path, index=False, encoding="utf-8-sig")
    print(f"✅ {latest_path} 저장 ({len(df_new)}건)")

    _archive_if_too_large(history_path)

    if not os.path.exists(history_path):
        df_new.to_csv(history_path, index=False, encoding="utf-8-sig")
        print(f"✅ {history_path} 최초 생성")
        return

    df_existing = pd.read_csv(history_path, encoding="utf-8-sig")
    for col in columns:
        if col not in df_existing.columns:
            df_existing[col] = ""
    df_existing = df_existing[columns]

    df_merged = pd.concat([df_existing, df_new], ignore_index=True).drop_duplicates()
    df_merged.to_csv(history_path, index=False, encoding="utf-8-sig")
    print(f"✅ {history_path} 누적 ({len(df_merged)}건 총)")


# =========================================================
# Main
# =========================================================

def run_parser():
    json_files = sorted(glob.glob('data/data_*.json'), reverse=True)
    if not json_files:
        print("❌ 분석할 데이터 파일이 없습니다.")
        return

    file_curr = json_files[0]
    with open(file_curr, 'r', encoding='utf-8') as f:
        raw_curr = json.load(f)
    raw_prev = {}
    if len(json_files) > 1:
        with open(json_files[1], 'r', encoding='utf-8') as f:
            raw_prev = json.load(f)

    validate_snapshot(raw_curr, raw_prev)
    warnings = snapshot_warnings(raw_curr, file_curr)
    raw_curr, raw_prev = comparison_snapshots(raw_curr, raw_prev)
    print(f"📂 최신: {file_curr}")
    m = re.search(r'data_(\d{8})_(\d{6})\.json', file_curr)
    timestamp = (
        datetime.strptime(f"{m.group(1)}_{m.group(2)}", "%Y%m%d_%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
        if m else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    print(f"📅 시각: {timestamp}\n")

    # ─────────────────────────────────────────────────────
    # 테이블 1: 이벤트 수집본 저장 (AI 없음)
    # ─────────────────────────────────────────────────────
    print("=" * 60)
    print("📋 테이블 1: 이벤트 수집본 저장")
    print("=" * 60)

    event_rows = []
    for company, items in raw_curr.items():
        for url, info in items.items():
            event_rows.append({
                "date":         timestamp,
                "company":      company,
                "title":        info.get('title', ''),
                "url":          url,
                "image":        info.get('img', ''),
                "category":     classify_category(info.get('title', '')),
            })

    df_events = pd.DataFrame(event_rows)
    safe_save(df_events, "data/dashboard_latest.csv", "data/dashboard_history.csv", EVENT_COLUMNS)

    # ─────────────────────────────────────────────────────
    # 테이블 2: 유의사항 수집 (금액 등 AI 추출 없음)
    # ─────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("🔍 테이블 2: 유의사항 수집")
    print("=" * 60)

    notice_rows = []

    for company, items in raw_curr.items():
        unique_notices = collect_unique_notices(items, company=company)
        print(f"\n🏢 [{company}] 유의사항 {len(unique_notices)}건 수집")

        for item in unique_notices:
            notice_rows.append({"date": timestamp, "company": company,
                                 "notice_text": item['notice'], "url": item["url"],
                                 "title": item["title"], **_NOTICE_DEFAULT})

    if notice_rows:
        df_notices = pd.DataFrame(notice_rows)
        print(f"\n📊 총 {len(df_notices)}건 수집")
        safe_save(df_notices, "data/notices_latest.csv", "data/notices_history.csv", NOTICE_COLUMNS)
    else:
        safe_save(pd.DataFrame(columns=NOTICE_COLUMNS), "data/notices_latest.csv",
                  "data/notices_history.csv", NOTICE_COLUMNS)

    # 이벤트 변경 + 유의사항 변경 분리 감지 → Slack
    total_chg, _, event_details = calculate_changes(raw_curr, raw_prev)
    notice_changes = calculate_notice_changes(raw_curr, raw_prev)
    send_slack_report(total_chg, event_details, notice_changes, warnings)


if __name__ == "__main__":
    run_parser()


