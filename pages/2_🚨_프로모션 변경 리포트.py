import streamlit as st
import glob
import os
import json
import difflib
import html
import re
import pandas as pd
from datetime import datetime

st.set_page_config(page_title="프로모션 변경 리포트", page_icon="🚨", layout="wide")

# =========================================================
# 데이터 로드
# =========================================================
@st.cache_data(ttl=600)
def get_available_dates():
    json_files = sorted(glob.glob("data/data_*.json"), reverse=True)
    dates = []
    weekday_kr = ['월', '화', '수', '목', '금', '토', '일']
    for f in json_files:
        date_str = os.path.basename(f).replace("data_", "").replace(".json", "")
        try:
            dt = datetime.strptime(date_str, "%Y%m%d_%H%M%S")
            weekday = weekday_kr[dt.weekday()]
            hour = dt.hour
            ampm = "오전" if hour < 12 else "오후"
            display_hour = hour if hour <= 12 else hour - 12
            display_hour = display_hour or 12
            display = f"{dt.month}월 {dt.day}일 ({weekday}) {ampm} {display_hour}:{dt.minute:02d}"
            dates.append({"file": f, "display": display, "raw": date_str, "datetime": dt})
        except Exception:
            dates.append({"file": f, "display": date_str, "raw": date_str, "datetime": None})
    return dates

@st.cache_data(ttl=600)
def load_data_by_file(file1, file2):
    try:
        with open(file1, 'r', encoding='utf-8') as f:
            d1 = json.load(f)
        with open(file2, 'r', encoding='utf-8') as f:
            d2 = json.load(f)
        return d1, d2
    except Exception:
        return None, None

def extract_benefit_amount(title):
    c = title.replace(",", "").replace(" ", "")
    try:
        m = re.search(r'(\d+(?:\.\d+)?)(?=만)', c)
        if m:
            return int(float(m.group(1)) * 10000)
        m = re.search(r'(\d+)(?=원)', c)
        if m:
            v = int(m.group(1))
            return v if v > 100 else 0
    except Exception:
        pass
    return 0

# =========================================================
# 이벤트 변경 감지 — 제목 + 이미지 + 본문 (유의사항은 별도 아키텍처)
# =========================================================
def detect_changes(old, new):
    changes = {}
    for field in ['title', 'img']:
        old_val = old.get(field, '') or ''
        new_val = new.get(field, '') or ''
        if old_val != new_val:
            changes[field] = {'old': old_val, 'new': new_val}
    return changes


# =========================================================
# 유의사항 변경 감지 — 회사별 줄 단위 집합 비교 (이벤트 감지와 독립)
# =========================================================
FOOTER_NOISE_KEYWORDS_P2 = [
    '이용약관', '개인정보 처리방침', '개인정보처리방침',
    '이메일 무단 수집거부', '분쟁처리절차', '프라이버시 센터',
    '유심구매하기', '다이렉트몰 구매하기', '오픈마켓 구매하기',
    '편의점/마트 구매하기', '요금제 소개', '요금제 비교',
    '전체 부가서비스', '로그인', '회원가입', '마이페이지',
    '없다면?', '있다면?', 'eSIM', '워치',
]

def _is_footer_noise(text: str) -> bool:
    return sum(1 for kw in FOOTER_NOISE_KEYWORDS_P2 if kw in text) >= 3

def _clean_html(text: str) -> str:
    if not text:
        return ''
    if '<' in text and '>' in text:
        from bs4 import BeautifulSoup
        text = BeautifulSoup(text, 'html.parser').get_text(separator='\n', strip=True)
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    return re.sub(r'\n{3,}', '\n\n', text).strip()

def get_notice_lines(data: dict, company: str) -> set:
    """
    회사의 모든 이벤트 notice를 수집 → HTML 정제 → 줄 단위 집합 반환.
    스카이라이프는 푸터 노이즈 필터 미적용.
    """
    skip_footer = (company == "스카이라이프")
    lines = set()
    for info in data.get(company, {}).values():
        raw = (info.get('notice', '') or '').strip()
        if not raw:
            continue
        cleaned = _clean_html(raw)
        if not cleaned:
            continue
        if not skip_footer and _is_footer_noise(cleaned):
            continue
        for line in cleaned.split('\n'):
            line = line.strip()
            if line and len(line) >= 10:
                lines.add(line)
    return lines

def calculate_notice_diff(data_curr: dict, data_prev: dict) -> dict:
    """
    회사별 유의사항 줄 단위 변경.
    반환: {company: {"added": [...], "removed": [...], "total": int}}
    """
    result = {}
    for company in set(data_curr.keys()) | set(data_prev.keys()):
        curr = get_notice_lines(data_curr, company)
        prev = get_notice_lines(data_prev, company)
        added   = sorted(curr - prev)
        removed = sorted(prev - curr)
        if added or removed:
            result[company] = {
                "added":   added,
                "removed": removed,
                "total":   len(added) + len(removed),
            }
    return result

# =========================================================
# Diff HTML
# =========================================================
def generate_diff_html(old_text, new_text, context_chars=80):
    old_text = (old_text or "").strip()
    new_text = (new_text or "").strip()
    if not old_text and not new_text:
        return None

    matcher = difflib.SequenceMatcher(None, old_text, new_text, autojunk=False)
    old_chunks, new_chunks = [], []
    has_change = False

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            continue
        has_change = True

        if tag in ('replace', 'delete'):
            s = max(0, i1 - context_chars)
            e = min(len(old_text), i2 + context_chars)
            chunk = (
                ("..." if s > 0 else "")
                + html.escape(old_text[s:i1])
                + f'<span style="background:#ffeef0;color:#c0392b;text-decoration:line-through;">'
                  f'{html.escape(old_text[i1:i2])}</span>'
                + html.escape(old_text[i2:e])
                + ("..." if e < len(old_text) else "")
            )
            old_chunks.append(chunk)

        if tag in ('replace', 'insert'):
            s = max(0, j1 - context_chars)
            e = min(len(new_text), j2 + context_chars)
            chunk = (
                ("..." if s > 0 else "")
                + html.escape(new_text[s:j1])
                + f'<span style="background:#e6fffa;color:#27ae60;font-weight:bold;">'
                  f'{html.escape(new_text[j1:j2])}</span>'
                + html.escape(new_text[j2:e])
                + ("..." if e < len(new_text) else "")
            )
            new_chunks.append(chunk)

    if not has_change:
        return None

    return f"""
    <div style="display:flex;gap:10px;font-size:13px;line-height:1.7;
                border:1px solid #ddd;border-radius:6px;overflow:hidden;">
        <div style="flex:1;background:#fffbfb;padding:12px;border-right:1px solid #eee;">
            <strong style="color:#c0392b;">❌ 이전</strong><br><br>
            {"<br>".join(old_chunks)}
        </div>
        <div style="flex:1;background:#fafffc;padding:12px;">
            <strong style="color:#27ae60;">✅ 현재</strong><br><br>
            {"<br>".join(new_chunks)}
        </div>
    </div>
    """

# =========================================================
# UI
# =========================================================
st.title("🚨 프로모션 변경 리포트")

available_dates = get_available_dates()
if len(available_dates) < 2:
    st.error("⚠️ 비교할 데이터가 부족합니다. 최소 2개의 데이터 파일이 필요합니다.")
    st.stop()

st.markdown("### 📅 비교 날짜 선택")
col_date1, col_date2 = st.columns(2)
with col_date1:
    date1_idx = st.selectbox(
        "기준 날짜 (최신)", range(len(available_dates)),
        format_func=lambda x: available_dates[x]['display'], index=0, key="date1"
    )
    date1_info = available_dates[date1_idx]
with col_date2:
    date2_idx = st.selectbox(
        "비교 날짜 (이전)", range(len(available_dates)),
        format_func=lambda x: available_dates[x]['display'], index=1, key="date2"
    )
    date2_info = available_dates[date2_idx]

st.caption(f"📊 **{date1_info['display']}** 기준으로 **{date2_info['display']}**와 비교 중")
if date1_info.get('datetime') and date2_info.get('datetime'):
    if date1_info['datetime'].date() == date2_info['datetime'].date():
        st.info("💡 같은 날짜(오전/오후) 비교입니다.")

data_today, data_yesterday = load_data_by_file(date1_info['file'], date2_info['file'])
if not data_today or not data_yesterday:
    st.error("❌ 데이터를 불러올 수 없습니다.")
    st.stop()

# =========================================================
# 이벤트 변경 데이터 수집
# =========================================================
all_companies = sorted(data_today.keys())
company_data = {}

for company in all_companies:
    urls_t = set(data_today.get(company, {}).keys())
    urls_y = set(data_yesterday.get(company, {}).keys())

    list_new = [
        {"company": company, "url": url, **data_today[company][url]}
        for url in urls_t - urls_y
    ]
    list_end = [
        {"company": company, "url": url, **data_yesterday[company][url]}
        for url in urls_y - urls_t
    ]
    list_mod = []
    for url in urls_t & urls_y:
        chg = detect_changes(data_yesterday[company][url], data_today[company][url])
        if chg:
            item = data_today[company][url].copy()
            item.update({"company": company, "url": url, "changes": chg})
            list_mod.append(item)

    company_data[company] = {
        "new": list_new, "modified": list_mod,
        "ended": list_end, "total": len(urls_t)
    }

# 유의사항 변경 데이터 (이벤트와 완전히 독립)
notice_diff = calculate_notice_diff(data_today, data_yesterday)

# =========================================================
# 검색 및 필터
# =========================================================
st.divider()
st.markdown("### 🔍 검색 및 필터")
col_search, col_filter = st.columns([3, 1])
with col_search:
    search_query = st.text_input("키워드 검색 (제목)", placeholder="예: 5만원, 친구추천, 신규가입", key="search")
with col_filter:
    show_only_changes = st.checkbox("변경사항만 보기", value=False, key="filter_changes")

# =========================================================
# 통신사 선택 버튼
# =========================================================
st.divider()
st.markdown("### 📊 통신사 선택")

if 'selected_company' not in st.session_state:
    st.session_state.selected_company = "전체"
if st.session_state.selected_company != "전체" and st.session_state.selected_company not in all_companies:
    st.session_state.selected_company = "전체"

btn_cols = st.columns(len(all_companies) + 1)
if btn_cols[0].button("🌐 전체", type="primary" if st.session_state.selected_company == "전체" else "secondary"):
    st.session_state.selected_company = "전체"
    st.rerun()

for idx, company in enumerate(all_companies, start=1):
    d = company_data[company]
    event_badge = len(d['new']) + len(d['modified']) + len(d['ended'])
    notice_badge = notice_diff.get(company, {}).get('total', 0)
    total_badge = event_badge + notice_badge
    label = f"{company} ({total_badge})" if total_badge > 0 else company
    if btn_cols[idx].button(label, type="primary" if st.session_state.selected_company == company else "secondary"):
        st.session_state.selected_company = company
        st.rerun()

# =========================================================
# CSV Export
# =========================================================
@st.cache_data
def prepare_export_data(company_data):
    rows = []
    for company, data in company_data.items():
        for item in data['new']:
            rows.append({"통신사": company, "상태": "🆕 신규", "제목": item.get('title', ''),
                         "URL": item.get('url', ''), "혜택금액": extract_benefit_amount(item.get('title', '')), "변경내용": "-"})
        for item in data['modified']:
            chg = item.get('changes', {})
            types = []
            if 'title' in chg: types.append("제목")
            if 'img' in chg: types.append("썸네일")
            rows.append({"통신사": company, "상태": "⚡ 변경", "제목": item.get('title', ''),
                         "URL": item.get('url', ''), "혜택금액": extract_benefit_amount(item.get('title', '')),
                         "변경내용": ", ".join(types) or "-"})
        for item in data['ended']:
            rows.append({"통신사": company, "상태": "🗑️ 종료", "제목": item.get('title', ''),
                         "URL": item.get('url', ''), "혜택금액": extract_benefit_amount(item.get('title', '')), "변경내용": "-"})
    return pd.DataFrame(rows)

export_df = prepare_export_data(company_data)
if not export_df.empty:
    csv = export_df.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig')
    st.download_button(
        "📥 변경사항 CSV 다운로드", data=csv,
        file_name=f"변경리포트_{date1_info['raw']}_vs_{date2_info['raw']}.csv",
        mime="text/csv", key="download_csv"
    )

st.divider()

# =========================================================
# 통신사별 섹션 출력
# =========================================================
target_companies = all_companies if st.session_state.selected_company == "전체" else [st.session_state.selected_company]

for company in target_companies:
    d = company_data[company]
    list_new  = d['new'][:]
    list_mod  = d['modified'][:]
    list_end  = d['ended'][:]
    total_cnt = d['total']

    if search_query:
        q = search_query.lower()
        list_new = [x for x in list_new if q in x.get('title', '').lower()]
        list_mod = [x for x in list_mod if q in x.get('title', '').lower()]
        list_end = [x for x in list_end if q in x.get('title', '').lower()]

    if show_only_changes:
        list_new = []
        list_end = []

    if not list_new and not list_mod and not list_end and not notice_diff.get(company):
        continue

    st.markdown(f"## 🏢 {company}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🆕 신규", f"{len(list_new)}건", delta=len(list_new) or None)
    c2.metric("⚡ 변경", f"{len(list_mod)}건", delta=len(list_mod) or None)
    c3.metric("🗑️ 종료", f"{len(list_end)}건",
              delta=-len(list_end) if list_end else None, delta_color="inverse")
    c4.metric("📊 전체", f"{total_cnt}건")

    # 신규
    if list_new:
        st.markdown(f"### 🆕 신규 등록 ({len(list_new)}건)")
        for item in list_new:
            with st.container(border=True):
                col1, col2 = st.columns([1, 5])
                with col1:
                    if item.get('img'):
                        try:
                            st.image(item['img'], width=150)
                        except Exception:
                            st.caption("이미지 로드 실패")
                    else:
                        st.caption("이미지 없음")
                with col2:
                    st.markdown(f"#### {item.get('title', '')}")
                    benefit = extract_benefit_amount(item.get('title', ''))
                    if benefit > 0:
                        st.caption(f"💰 혜택: **{benefit:,}원**")
                    st.markdown(f"👉 [바로가기]({item.get('url', '#')})")

    # 변경
    if list_mod:
        st.markdown(f"### ⚡ 변경 내역 ({len(list_mod)}건)")
        for item in list_mod:
            chg = item.get('changes', {})
            badges = []
            if 'title' in chg: badges.append("🏷️ 제목")
            if 'img' in chg: badges.append("🖼️ 썸네일")

            with st.expander(f"**{item.get('title', '')}** ({', '.join(badges) or '변경'})"):
                st.caption(f"🔗 [페이지 링크]({item.get('url', '#')})")

                if 'title' in chg:
                    st.markdown("**🏷️ 제목 변경**")
                    diff = generate_diff_html(chg['title']['old'], chg['title']['new'])
                    if diff:
                        st.markdown(diff, unsafe_allow_html=True)

                if 'img' in chg:
                    st.markdown("**🖼️ 썸네일 변경**")
                    ic1, ic2 = st.columns(2)
                    if chg['img']['old']:
                        try: ic1.image(chg['img']['old'], caption="이전", width=200)
                        except Exception: ic1.caption("이미지 로드 실패")
                    if chg['img']['new']:
                        try: ic2.image(chg['img']['new'], caption="현재", width=200)
                        except Exception: ic2.caption("이미지 로드 실패")

    # 종료
    if list_end:
        st.markdown(f"### 🗑️ 종료 확인 ({len(list_end)}건)")
        for item in list_end:
            st.error(f"❌ {item.get('title', '')}")

    # 유의사항 변경
    company_notice = notice_diff.get(company, {})
    added   = company_notice.get("added", [])
    removed = company_notice.get("removed", [])
    if added or removed:
        st.markdown(f"### 📝 유의사항 변경 (추가 {len(added)}줄 / 삭제 {len(removed)}줄)")
        if added:
            for sentence in added:
                st.markdown(
                    f"""<div style="background:#e6fffa;border-left:4px solid #27ae60;
                        padding:8px 12px;margin:4px 0;border-radius:0 6px 6px 0;
                        font-size:13px;line-height:1.6;">✅ {html.escape(sentence)}</div>""",
                    unsafe_allow_html=True
                )
        if removed:
            for sentence in removed:
                st.markdown(
                    f"""<div style="background:#fff5f5;border-left:4px solid #e74c3c;
                        padding:8px 12px;margin:4px 0;border-radius:0 6px 6px 0;
                        font-size:13px;line-height:1.6;text-decoration:line-through;color:#888;">
                        ❌ {html.escape(sentence)}</div>""",
                    unsafe_allow_html=True
                )

    st.divider()

# 변경사항 없는 경우
if st.session_state.selected_company == "전체":
    if all(len(d['new']) + len(d['modified']) + len(d['ended']) == 0 for d in company_data.values()):
        st.success("🎉 선택한 기간 동안 모든 통신사에서 이벤트 변경사항이 없습니다!")
else:
    sd = company_data.get(st.session_state.selected_company, {})
    if not sd.get('new') and not sd.get('modified') and not sd.get('ended'):
        st.info(f"🎉 **{st.session_state.selected_company}**에서 이벤트 변경사항이 없습니다.")
