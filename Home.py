"""
Home.py — 메인 대시보드
[변경]
- dashboard_latest.csv: 이벤트 수집본 (benefit_amt 없음)
- notices_latest.csv: 유의사항 분석 결과 (혜택 정보)
- 차트는 notices 기반, 카드는 이벤트 기반
"""

import streamlit as st
import pandas as pd
import altair as alt
import os
import glob
import json
from datetime import datetime, timedelta

st.set_page_config(page_title="메인 대시보드", page_icon="📱", layout="wide")


def get_mtime(path: str) -> float:
    return os.path.getmtime(path) if os.path.exists(path) else 0


@st.cache_data(ttl=60)
def load_events(mtime: float) -> pd.DataFrame:
    path = "data/dashboard_latest.csv"
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path)
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    df['image']    = df['image'].fillna('')
    df['category'] = df['category'].fillna('기타')
    return df


@st.cache_data(ttl=60)
def load_notices(mtime: float) -> pd.DataFrame:
    path = "data/notices_latest.csv"
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path)
    df['date']          = pd.to_datetime(df['date'], errors='coerce')
    df['benefit_amt']   = pd.to_numeric(df['benefit_amt'], errors='coerce').fillna(0)
    df['cond_plan_price'] = pd.to_numeric(df['cond_plan_price'], errors='coerce').fillna(0)
    return df


@st.cache_data(ttl=60)
def load_notices_history(mtime: float) -> pd.DataFrame:
    path = "data/notices_history.csv"
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path)
    df['date']        = pd.to_datetime(df['date'], errors='coerce')
    df['benefit_amt'] = pd.to_numeric(df['benefit_amt'], errors='coerce').fillna(0)
    return df


@st.cache_data(ttl=60)
def calculate_changes(json_mtime: float) -> dict:
    json_files = sorted(glob.glob("data/data_*.json"), reverse=True)
    if len(json_files) < 2:
        return {}
    with open(json_files[0], 'r', encoding='utf-8') as f:
        curr = json.load(f)
    with open(json_files[1], 'r', encoding='utf-8') as f:
        prev = json.load(f)
    changes = {}
    for company in set(curr) | set(prev):
        c_urls = set(curr.get(company, {}).keys())
        p_urls = set(prev.get(company, {}).keys())
        new = len(c_urls - p_urls)
        end = len(p_urls - c_urls)
        # 이벤트 변경 감지: 제목 + 이미지
        mod = sum(
            1 for url in c_urls & p_urls
            if curr[company][url].get('title') != prev[company][url].get('title')
            or curr[company][url].get('img') != prev[company][url].get('img')
        )
        total = new + end + mod
        if total > 0:
            changes[company] = {'new': new, 'end': end, 'mod': mod, 'total': total}
    return changes


# ── 데이터 로드 ───────────────────────────────────────────
df_events  = load_events(get_mtime("data/dashboard_latest.csv"))
df_notices = load_notices(get_mtime("data/notices_latest.csv"))
df_notices_hist = load_notices_history(get_mtime("data/notices_history.csv"))

json_files = sorted(glob.glob("data/data_*.json"), reverse=True)
changes = calculate_changes(get_mtime(json_files[0]) if json_files else 0)

# 통신사별 최대 혜택 (notices 기반, 카드에서 뱃지용)
company_max_benefit = {}
if not df_notices.empty:
    company_max_benefit = (
        df_notices[df_notices['benefit_amt'] > 0]
        .groupby('company')['benefit_amt']
        .max()
        .to_dict()
    )

# ── 헤더 ─────────────────────────────────────────────────
col_title, col_refresh = st.columns([6, 1])
with col_title:
    st.title("📱 경쟁사 모니터링 대시보드")
with col_refresh:
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("🔄 새로고침", type="secondary", use_container_width=True):
        load_events.clear()
        load_notices.clear()
        load_notices_history.clear()
        calculate_changes.clear()
        st.rerun()

update_time = df_events['date'].max().strftime('%Y-%m-%d %H:%M') if not df_events.empty else "N/A"
st.markdown(f"**최종 업데이트:** {update_time} | **수집 이벤트:** {len(df_events)}건")
st.divider()

if df_events.empty:
    st.warning("⚠️ 수집된 데이터가 없습니다. 크롤링을 먼저 실행해주세요.")
    st.stop()

# ── 1. 통신사별 변동 현황 ─────────────────────────────────
st.subheader("📊 통신사별 변동 현황")
st.caption("전일 대비 이벤트 변동 내역 (신규/종료/수정)")

all_companies = sorted(df_events['company'].unique())
company_list = sorted(
    [(c, changes.get(c, {'new': 0, 'end': 0, 'mod': 0, 'total': 0})) for c in all_companies],
    key=lambda x: x[1]['total'], reverse=True
)

for i in range(0, len(company_list), 3):
    cols = st.columns(3)
    for idx, (company, cd) in enumerate(company_list[i:i+3]):
        with cols[idx]:
            if cd['total'] > 0:
                parts = []
                if cd['new']: parts.append(f"신규 {cd['new']}")
                if cd['end']: parts.append(f"종료 {cd['end']}")
                if cd['mod']: parts.append(f"수정 {cd['mod']}")
                summary_text = " | ".join(parts)
                border_color = "#1f77b4"
                status_text  = f"{cd['total']}건 변동"
            else:
                summary_text = "변동 없음"
                border_color = "#999"
                status_text  = "변동 없음"

            st.markdown(f"""
            <div style="background:#f0f2f6;padding:15px;border-radius:8px;
                border-left:4px solid {border_color};margin-bottom:6px;">
                <h4 style="margin:0 0 8px 0;font-size:16px;">{company}</h4>
                <p style="margin:0;font-size:24px;font-weight:bold;color:{border_color};">{status_text}</p>
                <p style="margin:5px 0 0 0;font-size:13px;color:#555;">{summary_text}</p>
            </div>""", unsafe_allow_html=True)

            if cd['total'] > 0:
                if st.button("🔍 상세 보기", key=f"btn_{company}", type="primary", use_container_width=True):
                    st.session_state.selected_company = company
                    st.switch_page("pages/2_🚨_프로모션 변경 리포트.py")

st.caption("💡 상세 변동 내역은 **🚨프로모션 변경 리포트** 페이지를 이용하세요")
st.divider()

# ── 2. 혜택 차트 (notices 기반) ───────────────────────────
col1, col2 = st.columns(2)

with col1:
    st.subheader("통신사별 최대 혜택 금액")
    st.caption("유의사항 분석 기반 — 통신사별 단일 최대 혜택")
    if not df_notices.empty and df_notices['benefit_amt'].max() > 0:
        df_max = (
            df_notices[df_notices['benefit_amt'] > 0]
            .groupby('company')['benefit_amt']
            .max()
            .reset_index()
            .sort_values('benefit_amt', ascending=False)
        )
        chart = alt.Chart(df_max).mark_bar().encode(
            x=alt.X('company:N', title='통신사', sort='-y'),
            y=alt.Y('benefit_amt:Q', title='최대 혜택 (원)', axis=alt.Axis(format='~s')),
            color=alt.Color('company:N', legend=None, scale=alt.Scale(scheme='category10')),
            tooltip=[
                alt.Tooltip('company:N', title='통신사'),
                alt.Tooltip('benefit_amt:Q', title='혜택 금액', format=',')
            ]
        ).properties(height=320)
        st.altair_chart(chart, use_container_width=True)
    else:
        st.info("📊 유의사항 분석 데이터가 없습니다.")

with col2:
    st.subheader("혜택 금액 추이 (최근 30일)")
    st.caption("통신사별 최대 혜택 변화")
    if not df_notices_hist.empty and df_notices_hist['date'].nunique() > 1:
        cutoff = pd.Timestamp.now() - timedelta(days=30)
        df_trend = (
            df_notices_hist[df_notices_hist['date'] >= cutoff]
            .groupby([df_notices_hist['date'].dt.date, 'company'])['benefit_amt']
            .max()
            .reset_index()
        )
        df_trend.columns = ['date', 'company', 'benefit_amt']
        chart_line = alt.Chart(df_trend).mark_line(point=True).encode(
            x=alt.X('date:T', title='날짜'),
            y=alt.Y('benefit_amt:Q', title='최대 혜택 (원)', axis=alt.Axis(format='~s')),
            color=alt.Color('company:N', title='통신사'),
            tooltip=[
                alt.Tooltip('date:T', title='날짜', format='%Y-%m-%d'),
                alt.Tooltip('company:N', title='통신사'),
                alt.Tooltip('benefit_amt:Q', title='혜택 금액', format=',')
            ]
        ).properties(height=320)
        st.altair_chart(chart_line, use_container_width=True)
    else:
        st.info("📊 2회차 수집 이후 추이 차트가 표시됩니다.")

st.divider()

# ── 3. 통신사별 이벤트 카드 ──────────────────────────────
st.subheader("📋 통신사별 이벤트 상세")

col1, col2 = st.columns([3, 1])
with col1:
    selected_company = st.selectbox("🏢 통신사 선택", ["전체"] + sorted(df_events['company'].unique()), key="company_filter")
with col2:
    sort_option = st.selectbox("정렬", ["최신 순", "이름 순"], key="sort_option")

filtered = df_events.copy() if selected_company == "전체" else df_events[df_events['company'] == selected_company].copy()
filtered = filtered.sort_values('date', ascending=False) if sort_option == "최신 순" else filtered.sort_values('title')

st.caption(f"총 {len(filtered)}개 이벤트")
st.divider()

CATEGORY_COLORS = {
    "친구추천": "#9b59b6", "가입혜택": "#e74c3c",
    "요금제": "#3498db",   "리뷰이벤트": "#f39c12", "기타": "#95a5a6"
}


def _placeholder(company: str):
    st.markdown(
        f'<div style="width:100%;height:100px;background:linear-gradient(135deg,#667eea,#764ba2);'
        f'display:flex;align-items:center;justify-content:center;border-radius:8px 8px 0 0;">'
        f'<span style="color:white;font-size:20px;font-weight:bold;">{company[:2]}</span></div>',
        unsafe_allow_html=True
    )


def show_card(row, show_company: bool = False):
    thumb    = str(row.image) if str(row.image) not in ('', 'nan') else ''
    category = str(row.category) if str(row.category) not in ('', 'nan') else '기타'
    badge_color = CATEGORY_COLORS.get(category, "#95a5a6")
    max_benefit = company_max_benefit.get(row.company, 0)

    with st.container():
        if thumb.startswith('http'):
            try:
                st.image(thumb, use_container_width=True)
            except Exception:
                _placeholder(row.company)
        else:
            _placeholder(row.company)

        if show_company:
            st.caption(f"🏢 {row.company}")

        st.markdown(
            f'<span style="background:{badge_color};color:white;padding:3px 8px;'
            f'border-radius:10px;font-size:10px;font-weight:bold;">{category}</span>',
            unsafe_allow_html=True
        )
        title_short = row.title[:35] + "..." if len(row.title) > 35 else row.title
        st.markdown(
            f'<div style="height:40px;overflow:hidden;margin:8px 0;">'
            f'<strong style="font-size:13px;line-height:1.3;">{title_short}</strong></div>',
            unsafe_allow_html=True
        )
        if max_benefit > 0:
            st.markdown(
                f'<div style="background:#e74c3c;color:white;padding:8px;border-radius:6px;'
                f'text-align:center;margin:8px 0;">'
                f'<div style="font-size:9px;opacity:0.9;">통신사 최대 혜택</div>'
                f'<div style="font-size:16px;font-weight:bold;">{int(max_benefit):,}원</div></div>',
                unsafe_allow_html=True
            )
        else:
            st.markdown(
                '<div style="background:#95a5a6;color:white;padding:8px;border-radius:6px;'
                'text-align:center;margin:8px 0;"><div style="font-size:11px;">혜택 정보 없음</div></div>',
                unsafe_allow_html=True
            )
        st.link_button("🔗 바로가기", row.url, use_container_width=True, type="primary")
        st.markdown("<br>", unsafe_allow_html=True)


if selected_company == "전체":
    for company in sorted(filtered['company'].unique()):
        cdf = filtered[filtered['company'] == company]
        with st.expander(f"🏢 **{company}** ({len(cdf)}건)", expanded=False):
            for i in range(0, len(cdf), 4):
                cols = st.columns(4)
                for idx, row in enumerate(cdf.iloc[i:i+4].itertuples()):
                    with cols[idx]:
                        show_card(row, show_company=False)
else:
    for i in range(0, len(filtered), 4):
        cols = st.columns(4)
        for idx, row in enumerate(filtered.iloc[i:i+4].itertuples()):
            with cols[idx]:
                show_card(row, show_company=True)

st.divider()
st.caption("💡 상세 변동 내역은 **🚨프로모션 변경 리포트**를, 과거 이력은 **🗄️이벤트 히스토리 DB**를 이용하세요")
