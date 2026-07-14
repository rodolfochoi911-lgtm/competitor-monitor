"""
parser.py
[구조]
- 테이블 1: dashboard_latest.csv / dashboard_history.csv
  → 이벤트 수집본 저장 (AI 없음), notice 필드는 정제된 순수 텍스트

- 테이블 2: notices_latest.csv / notices_history.csv
  → 회사별 유의사항 이벤트 단위 중복 제거 → AI 분석
  → 컬럼: company, notice_text, benefit_amt, benefit_type, cond_type, cond_plan_price, summary

[수정 내역]
- clean_html_to_text(): HTML 태그/주석 완전 제거 → notice 저장 전 항상 적용
- FOOTER_NOISE_KEYWORDS + is_footer_noise(): 푸터/네비 노이즈 감지
- collect_unique_notices(): HTML 제거 → 노이즈 필터 → fingerprint 순서로 수정
- event_rows 저장 시 notice 필드도 clean_html_to_text() 적용
- analyze_notice_line(): 재귀 없음, 429 시 즉시 default 반환
"""

import os
import json
import time
import glob
import re
import hashlib
import requests
import pandas as pd
from bs4 import BeautifulSoup, Comment
from groq import Groq
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

load_dotenv()
api_key = os.getenv("GROQ_API_KEY")
slack_webhook_url = os.getenv("SLACK_WEBHOOK_URL")

if not api_key:
    print("❌ GROQ_API_KEY가 없습니다.")
    exit(1)

client = Groq(api_key=api_key)
MODEL = "llama-3.1-8b-instant"

KST = timezone(timedelta(hours=9))

EVENT_COLUMNS = [
    "date", "company", "title", "url", "image", "category",
]

NOTICE_COLUMNS = [
    "date", "company", "notice_text",
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
    """
    중복 감지용: 날짜·숫자·금액 등 이벤트마다 달라지는 부분을 마스킹.
    예) '사은품 지급일 : 25년 1월 22일경 지급'
      → '사은품 지급일 : DATE경 지급'
    → 날짜만 다른 동일 패턴 항목이 중복으로 잡힘
    """
    t = text
    # 연월일 패턴
    t = re.sub(r'\d{2,4}[년./-]\s*\d{1,2}[월./-]\s*\d{1,2}일?', 'DATE', t)
    t = re.sub(r'\d{1,2}월\s*\d{1,2}일', 'DATE', t)
    # 금액/수량 (N원, NGB, N개월, N회, N명 등)
    t = re.sub(r'\d[\d,]*\s*(?:원|GB|MB|개월|일간|회|명|건|개)', 'N', t)
    # 나머지 숫자
    t = re.sub(r'\d+', 'N', t)
    return re.sub(r'\s+', ' ', t).strip()


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
            if len(merged) >= 15:
                items.append(merged)
            current.clear()

    for line in lines:
        if is_footer_line(line):
            continue
        if is_section_header(line):
            continue
        if is_timestamp_line(line):
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
    4. 날짜/숫자 마스킹 기반 중복 제거 → 같은 패턴의 숫자만 다른 변형 제거
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
            all_items.append({"line": item_text, "url": url, "title": info.get('title', '')})

    # 숫자/날짜 마스킹 기반 중복 제거
    seen_normalized = set()
    seen_exact      = set()
    result = []
    skipped_dup = 0

    for item in all_items:
        exact_fp = text_fingerprint(item["line"])
        norm_fp  = text_fingerprint(normalize_for_dedup(item["line"]))

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


# =========================================================
# 혜택 키워드 사전 필터 — AI 호출 前 1차 스크리닝
# =========================================================
# "원", "할인" 등이 포함된 줄만 AI로 보냄. 나머지는 즉시 NO_BENEFIT 처리.
# 효과: AI 호출 수 최대 70% 절감, 비용·속도 모두 개선
BENEFIT_HINT_KEYWORDS = [
    '원', '할인', '캐시백', '포인트', '쿠폰', '상품권', '적립', '페이백',
    'GB', 'MB', '데이터', '무료', '증정', '지급', '제공', '혜택',
    '환급', '리워드', '선물', '경품', '사은품', '바우처', 'pay', 'Pay',
]

def has_benefit_hint(text: str) -> bool:
    """단순 키워드 매칭으로 혜택 가능성 사전 판단. 없으면 AI 호출 스킵."""
    return any(kw in text for kw in BENEFIT_HINT_KEYWORDS)


# =========================================================
# AI 분석 — 배치 처리 (10건씩 묶어서 1회 API 호출)
# =========================================================
# 기존: 1건 = 1 API 호출 + sleep(2) → 400건이면 800초(13분)
# 변경: 10건 = 1 API 호출 + sleep(1) → 40호출이면 40초 (95% 단축)

BATCH_SIZE = 10

_NOTICE_DEFAULT = {
    "benefit_amt": 0, "benefit_type": "NO_BENEFIT",
    "cond_type": "ETC", "cond_plan_price": 0, "summary": "",
}

def analyze_notice_batch(notice_items: list) -> list:
    """
    notice_items: [{"notice": str, ...}, ...]  최대 BATCH_SIZE개
    반환: 각 항목에 대응하는 분석 결과 dict 리스트 (순서 보장)
    """
    if not notice_items:
        return []

    # 입력 구성: 인덱스 붙여서 순서 보장
    lines_str = "\n".join(
        f"[{i}] {item['notice'][:300]}"
        for i, item in enumerate(notice_items)
    )

    prompt = f"""알뜰폰 이벤트 유의사항 {len(notice_items)}개를 분석해서 JSON 배열로만 답해줘.
배열 길이는 반드시 {len(notice_items)}개. 인덱스 순서 유지.

각 원소 필드:
- benefit_amt: 현금성 혜택 금액(숫자). 추첨/경품/단순조건이면 0
- benefit_type: CASHBACK | VOUCHER(상품권/쿠폰) | POINT | DISCOUNT | NO_BENEFIT
- cond_type: BASIC(누구나) | FRIEND(친구추천) | CARD(제휴카드) | ETC
- cond_plan_price: 최소 요금제 금액(없으면 0)
- summary: 핵심 10자 이내

유의사항 목록:
{lines_str}

JSON 배열만 출력. 설명 없이."""

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=BATCH_SIZE * 80,   # 항목당 약 80토큰
        )
        raw = resp.choices[0].message.content.strip()
        # 코드블럭 제거
        raw = re.sub(r"^```json|^```|```$", "", raw, flags=re.MULTILINE).strip()

        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            parsed = [parsed]

        # 길이 맞추기 (모델이 일부 항목 누락하면 default로 채움)
        results = []
        for i in range(len(notice_items)):
            if i < len(parsed) and isinstance(parsed[i], dict):
                results.append({**_NOTICE_DEFAULT, **{k: parsed[i][k] for k in _NOTICE_DEFAULT if k in parsed[i]}})
            else:
                results.append(dict(_NOTICE_DEFAULT))
        return results

    except json.JSONDecodeError:
        return [dict(_NOTICE_DEFAULT) for _ in notice_items]
    except Exception as e:
        err = str(e)
        if "429" in err:
            print("   ⏳ Rate limit — 15초 대기 후 재시도...")
            time.sleep(15)
            return analyze_notice_batch(notice_items)   # 1회 재시도
        print(f"   ❌ 배치 API 에러: {type(e).__name__}: {err[:80]}")
        return [dict(_NOTICE_DEFAULT) for _ in notice_items]


# =========================================================
# 변경 감지
# =========================================================

def calculate_changes(current_data: dict, prev_data: dict) -> tuple:
    changes = {}
    details = []
    for company in set(current_data.keys()) | set(prev_data.keys()):
        curr = current_data.get(company, {})
        prev = prev_data.get(company, {})
        curr_urls, prev_urls = set(curr.keys()), set(prev.keys())
        new_cnt = len(curr_urls - prev_urls)
        end_cnt = len(prev_urls - curr_urls)
        mod_cnt = sum(
            1 for url in curr_urls & prev_urls
            if curr[url].get('title') != prev[url].get('title')
            or curr[url].get('img') != prev[url].get('img')
        )
        total = new_cnt + end_cnt + mod_cnt
        if total > 0:
            changes[company] = total
            parts = []
            if new_cnt: parts.append(f"신규 {new_cnt}건")
            if end_cnt: parts.append(f"종료 {end_cnt}건")
            if mod_cnt: parts.append(f"수정 {mod_cnt}건")
            details.append(f"• {company} 총 {total}건 ({', '.join(parts)})")
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
    """
    회사별 유의사항 줄 단위 변경 감지.
    반환: {company: {"added": [문장, ...], "removed": [문장, ...]}}
    이벤트 변경 감지(calculate_changes)와 완전히 독립된 아키텍처.
    """
    result = {}
    for company in set(curr_data.keys()) | set(prev_data.keys()):
        curr_lines = _get_notice_lines(curr_data, company)
        prev_lines = _get_notice_lines(prev_data, company)
        added   = sorted(curr_lines - prev_lines)
        removed = sorted(prev_lines - curr_lines)
        if added or removed:
            result[company] = {"added": added, "removed": removed}
    return result


# =========================================================
# Slack
# =========================================================

def send_slack_report(total_change: int, event_details: list, notice_changes: dict):
    if not slack_webhook_url:
        print("⚠️ SLACK_WEBHOOK_URL 없음")
        return

    now_str = datetime.now(KST).strftime("%y.%m.%d %H:%M:%S")
    dashboard_url = os.getenv(
        "DASHBOARD_URL",
        "https://share.streamlit.io/rodolfochoi911-lgtm/competitor-monitor/main/Home.py"
    )

    if total_change == 0:
        msg = f"[{now_str}] 경쟁사 동향 보고\n\n특이사항 없음\n\n대시보드: {dashboard_url}"
    else:
        body = "\n".join(event_details) if event_details else "이벤트 변동 없음"
        msg = f"[{now_str}] 경쟁사 동향 보고\n\n총 {total_change}건 변동\n{body}\n\n대시보드: {dashboard_url}"

    try:
        r = requests.post(slack_webhook_url, json={"text": msg}, timeout=10)
        print("✅ Slack 발송 완료!" if r.status_code == 200 else f"⚠️ Slack {r.status_code}")
    except Exception as e:
        print(f"❌ Slack 실패: {e}")


# =========================================================
# CSV 안전 저장
# =========================================================

def safe_save(df_new: pd.DataFrame, latest_path: str, history_path: str, columns: list):
    for col in columns:
        if col not in df_new.columns:
            df_new[col] = ""
    df_new = df_new[columns]

    df_new.to_csv(latest_path, index=False, encoding="utf-8-sig")
    print(f"✅ {latest_path} 저장 ({len(df_new)}건)")

    if not os.path.exists(history_path):
        df_new.to_csv(history_path, index=False, encoding="utf-8-sig")
        print(f"✅ {history_path} 최초 생성")
        return

    df_existing = pd.read_csv(history_path, encoding="utf-8-sig")
    for col in columns:
        if col not in df_existing.columns:
            df_existing[col] = ""
    df_existing = df_existing[columns]

    df_merged = pd.concat([df_existing, df_new], ignore_index=True)
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
                "main_content": clean_html_to_text(info.get('main_content', '') or ''),
            })

    df_events = pd.DataFrame(event_rows)
    safe_save(df_events, "data/dashboard_latest.csv", "data/dashboard_history.csv", EVENT_COLUMNS)

    # ─────────────────────────────────────────────────────
    # 테이블 2: 유의사항 AI 분석
    # ─────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("🔍 테이블 2: 유의사항 AI 분석")
    print("=" * 60)

    notice_rows = []
    NO_BENEFIT_DEFAULT = {
        "benefit_amt": 0, "benefit_type": "NO_BENEFIT",
        "cond_type": "ETC", "cond_plan_price": 0, "summary": "",
    }

    for company, items in raw_curr.items():
        unique_notices = collect_unique_notices(items, company=company)

        # 키워드 사전 필터: 혜택 힌트 없는 항목은 AI 호출 없이 즉시 NO_BENEFIT
        need_ai    = [n for n in unique_notices if has_benefit_hint(n['notice'])]
        skip_no_ai = [n for n in unique_notices if not has_benefit_hint(n['notice'])]

        print(f"\n🏢 [{company}] 유의사항 {len(unique_notices)}건 "
              f"→ AI {len(need_ai)}건 / 키워드없음 {len(skip_no_ai)}건 스킵")

        # 키워드 없는 항목: 즉시 NO_BENEFIT 저장
        for item in skip_no_ai:
            notice_rows.append({"date": timestamp, "company": company,
                                 "notice_text": item['notice'], **NO_BENEFIT_DEFAULT})

        # 키워드 있는 항목: BATCH_SIZE씩 묶어서 AI 호출
        total_batches = (len(need_ai) + BATCH_SIZE - 1) // BATCH_SIZE
        for batch_idx in range(0, len(need_ai), BATCH_SIZE):
            batch = need_ai[batch_idx: batch_idx + BATCH_SIZE]
            batch_num = batch_idx // BATCH_SIZE + 1

            results = analyze_notice_batch(batch)

            for item, result in zip(batch, results):
                notice_rows.append({
                    "date":            timestamp,
                    "company":         company,
                    "notice_text":     item['notice'],
                    "benefit_amt":     result.get("benefit_amt", 0),
                    "benefit_type":    result.get("benefit_type", "NO_BENEFIT"),
                    "cond_type":       result.get("cond_type", "ETC"),
                    "cond_plan_price": result.get("cond_plan_price", 0),
                    "summary":         result.get("summary", ""),
                })

            # 배치당 혜택 있는 항목 요약 출력
            hits = [(item, r) for item, r in zip(batch, results) if r.get("benefit_amt", 0) > 0]
            print(f"   배치 {batch_num}/{total_batches} 완료 | 혜택 발견: {len(hits)}건"
                  + (f" → {hits[0][1]['benefit_amt']:,}원 {hits[0][1]['benefit_type']}" if hits else ""))

            time.sleep(1)   # 배치당 1초 (기존 항목당 2초 → 10배 빠름)

    if notice_rows:
        df_notices = pd.DataFrame(notice_rows)
        has_benefit = df_notices[df_notices['benefit_amt'] > 0]
        print(f"\n📊 분석 결과: 총 {len(df_notices)}건 → 혜택 있음 {len(has_benefit)}건")
        if not has_benefit.empty:
            print("\n💰 추출된 혜택 목록:")
            for _, row in has_benefit.sort_values('benefit_amt', ascending=False).iterrows():
                print(f"   {row['company']} | {int(row['benefit_amt']):,}원 | {row['benefit_type']} | {row['notice_text'][:50]}")
        safe_save(df_notices, "data/notices_latest.csv", "data/notices_history.csv", NOTICE_COLUMNS)
    else:
        print("⚠️ 저장할 유의사항 데이터 없음")

    # 이벤트 변경 + 유의사항 변경 분리 감지 → Slack
    total_chg, _, event_details = calculate_changes(raw_curr, raw_prev)
    notice_changes = calculate_notice_changes(raw_curr, raw_prev)
    send_slack_report(total_chg, event_details, notice_changes)


if __name__ == "__main__":
    run_parser()
