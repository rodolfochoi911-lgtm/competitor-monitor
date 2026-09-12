"""
이벤트 DB — 계층 탐색 + 히스토리 통합 페이지
날짜 → 회차 → 통신사 → 이벤트 구조 + 통계 분석 + 이벤트 추적
"""

import streamlit as st
import pandas as pd
import altair as alt
import os
from datetime import datetime

st.set_page_config(page_title="이벤트 DB", page_icon="🗄️", layout="wide")

# =========================================================
# 1. 데이터 로드
# =========================================================
@st.cache_data(ttl=300)
def load_history_data():
    history_path = "data/dashboard_history.csv"
    if not os.path.exists(history_path):
        return pd.DataFrame()

    df = pd.read_csv(history_path, encoding='utf-8-sig')

    # 무거운 컬럼 즉시 제거 (main_content: 이벤트당 최대 50KB HTML → 렌더링 불가)
    for heavy_col in ['main_content', 'full_text']:
        if heavy_col in df.columns:
            df.drop(columns=[heavy_col], inplace=True)

    # 날짜 포맷 혼재 대응: 초 없는 '2026-02-10 07:01'과 초 있는 '2026-02-12 11:14:39' 모두 처리
    df['date'] = pd.to_datetime(df['date'], format='mixed', errors='coerce')
    df = df.dropna(subset=['date'])
    df = df.sort_values('date', ascending=False)
    df['date_only'] = df['date'].dt.date
    df['crawl_round'] = df['date'].dt.floor('2h').dt.strftime('%Y-%m-%d %H:%M')
    df['time_str'] = df['date'].dt.floor('2h').dt.strftime('%H:%M')

    # benefit_amt 컬럼 없으면 0으로 채움
    if 'benefit_amt' not in df.columns:
        df['benefit_amt'] = 0
    else:
        df['benefit_amt'] = pd.to_numeric(df['benefit_amt'], errors='coerce').fillna(0)

    return df

df = load_history_data()

if df.empty:
    st.error("⚠️ 히스토리 데이터가 없습니다. 크롤링을 먼저 실행해주세요.")
    st.stop()

HAS_BENEFIT = 'benefit_amt' in df.columns and df['benefit_amt'].sum() > 0

# =========================================================
# 2. 사이드바 필터
# =========================================================
st.sidebar.header("🔍 필터")

date_filter = st.sidebar.radio("날짜 범위", ["전체", "최근 7일", "최근 30일", "사용자 지정"])

filtered_df = df.copy()

if date_filter == "최근 7일":
    filtered_df = filtered_df[filtered_df['date'] >= pd.Timestamp.now() - pd.Timedelta(days=7)]
elif date_filter == "최근 30일":
    filtered_df = filtered_df[filtered_df['date'] >= pd.Timestamp.now() - pd.Timedelta(days=30)]
elif date_filter == "사용자 지정":
    try:
        min_date = df['date'].min().date()
        max_date = df['date'].max().date()
        start_date = st.sidebar.date_input("시작일", min_date, min_value=min_date, max_value=max_date)
        end_date = st.sidebar.date_input("종료일", max_date, min_value=min_date, max_value=max_date)
        filtered_df = filtered_df[
            (filtered_df['date_only'] >= start_date) &
            (filtered_df['date_only'] <= end_date)
        ]
    except Exception as e:
        st.sidebar.warning(f"날짜 필터 오류: {str(e)[:50]}")

st.sidebar.markdown("---")
all_companies = sorted(df['company'].unique())
selected_companies = st.sidebar.multiselect("통신사 필터 (선택 안하면 전체)", all_companies, default=[])
if selected_companies:
    filtered_df = filtered_df[filtered_df['company'].isin(selected_companies)]

if HAS_BENEFIT:
    min_benefit = st.sidebar.number_input("최소 혜택 금액", 0, 1000000, 0, 10000)
    if min_benefit > 0:
        filtered_df = filtered_df[filtered_df['benefit_amt'] >= min_benefit]

st.sidebar.markdown("---")
st.sidebar.subheader("📊 현재 조회 통계")
try:
    avg_benefit = f"{filtered_df['benefit_amt'].mean():,.0f}원" if HAS_BENEFIT else "N/A"
    st.sidebar.markdown(f"""
- 총 이벤트: **{len(filtered_df):,}건**
- 고유 이벤트: **{filtered_df['url'].nunique()}개**
- 통신사: **{filtered_df['company'].nunique()}개**
- 수집 회차: **{filtered_df['crawl_round'].nunique()}회**
- 날짜 범위: **{len(filtered_df['date_only'].unique())}일**
- 평균 혜택: **{avg_benefit}**
""")
    st.sidebar.markdown("#### 🏆 통신사별 이벤트 수")
    for company, count in filtered_df['company'].value_counts().head(5).items():
        st.sidebar.markdown(f"- {company}: **{count}건**")
except Exception:
    pass

st.sidebar.markdown("---")
st.sidebar.subheader("⚙️ 데이터 관리")
if st.sidebar.button("📦 전체 히스토리 CSV 다운로드"):
    full_csv = df.to_csv(index=False, encoding='utf-8-sig')
    st.sidebar.download_button(
        "다운로드 시작",
        full_csv,
        f"full_history_{datetime.now().strftime('%Y%m%d')}.csv",
        "text/csv"
    )

# =========================================================
# 3. 헤더 + 전체 통계
# =========================================================
col_title, col_refresh = st.columns([6, 1])
with col_title:
    st.title("🗄️ 이벤트 DB")
with col_refresh:
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("🔄 새로고침", type="secondary", use_container_width=True):
        load_history_data.clear()
        st.rerun()

total_rounds = df['crawl_round'].nunique()
c1, c2, c3, c4 = st.columns(4)
with c1: st.metric("총 크롤링 횟수", f"{total_rounds}회")
with c2: st.metric("고유 이벤트 수", f"{df['url'].nunique()}개")
with c3: st.metric("전체 레코드", f"{len(df):,}건")
with c4: st.metric("회차당 평균", f"{len(df)/max(total_rounds,1):.0f}건")

st.divider()

# =========================================================
# 4. 탭 구성
# =========================================================
tab1, tab2, tab3, tab4 = st.tabs(["🗂️ 계층 탐색", "📋 전체 레코드", "📊 통계 분석", "🔍 이벤트 추적"])

# ─────────────────────────────────────────────────────────
# Tab 1: 계층 탐색
# ─────────────────────────────────────────────────────────
with tab1:
    st.caption("날짜 → 수집 회차 → 통신사 → 이벤트 순서로 탐색")

    date_list = sorted(filtered_df['date_only'].unique(), reverse=True)

    if not date_list:
        st.warning("조건에 맞는 데이터가 없습니다.")
    else:
        for date_idx, date in enumerate(date_list):
            day_df = filtered_df[filtered_df['date_only'] == date]
            round_count = day_df['crawl_round'].nunique()
            event_count = len(day_df)
            company_count = day_df['company'].nunique()

            try:
                date_str = date.strftime('%Y년 %m월 %d일 (%a)')
            except Exception:
                date_str = str(date)

            date_header = f"📅 **{date_str}** — 수집 {round_count}회차 · 이벤트 {event_count}건 · {company_count}개 통신사"

            with st.expander(date_header, expanded=(date_idx == 0)):
                rounds = sorted(day_df['crawl_round'].unique(), reverse=True)

                for round_idx, round_str in enumerate(rounds):
                    round_df = day_df[day_df['crawl_round'] == round_str]

                    try:
                        round_time = datetime.strptime(round_str, '%Y-%m-%d %H:%M').strftime('%H:%M')
                    except Exception:
                        round_time = round_str

                    st.markdown(f"##### 🕐 {round_time} 회차 · {len(round_df)}건 · {round_df['company'].nunique()}개 통신사")

                    companies_in_round = sorted(round_df['company'].unique())
                    tab_labels = [f"📱 {c} ({len(round_df[round_df['company']==c])}건)" for c in companies_in_round]
                    company_tabs = st.tabs(tab_labels) if len(companies_in_round) > 1 else [st.container()]

                    for t_idx, company in enumerate(companies_in_round):
                        company_df = round_df[round_df['company'] == company]
                        if HAS_BENEFIT:
                            company_df = company_df.sort_values('benefit_amt', ascending=False)

                        with company_tabs[t_idx]:
                            m1, m2, m3 = st.columns(3)
                            with m1:
                                st.metric("이벤트 수", f"{len(company_df)}건")
                            with m2:
                                if HAS_BENEFIT:
                                    st.metric("최대 혜택", f"{int(company_df['benefit_amt'].max()):,}원")
                                else:
                                    st.metric("최대 혜택", "N/A")
                            with m3:
                                if HAS_BENEFIT:
                                    st.metric("평균 혜택", f"{int(company_df['benefit_amt'].mean()):,}원")
                                else:
                                    st.metric("평균 혜택", "N/A")

                            st.markdown("---")

                            view_mode = st.radio(
                                "표시 방식",
                                ["테이블", "카드 뷰"],
                                key=f"view_{date}_{round_str}_{company}",
                                horizontal=True
                            )

                            # 표시할 컬럼 동적 구성 (main_content/notice는 렌더링 제외)
                            HIDDEN_COLS = {'main_content', 'notice'}
                            table_cols = ['title', 'url']
                            col_config = {
                                "title": st.column_config.TextColumn("이벤트명", width="large"),
                                "url": st.column_config.LinkColumn("링크", width="small"),
                            }
                            for c, cfg in [
                                ('benefit_amt', st.column_config.NumberColumn("혜택", format="%d원", width="small")),
                                ('benefit_type', st.column_config.TextColumn("유형", width="small")),
                                ('cond_type', st.column_config.TextColumn("조건", width="small")),
                                ('ai_summary', st.column_config.TextColumn("요약", width="medium")),
                            ]:
                                if c in company_df.columns and c not in HIDDEN_COLS:
                                    table_cols.insert(-1, c)
                                    col_config[c] = cfg

                            if view_mode == "테이블":
                                st.dataframe(
                                    company_df[[c for c in table_cols if c in company_df.columns]],
                                    column_config=col_config,
                                    hide_index=True,
                                    use_container_width=True
                                )
                            else:
                                for _, event in company_df.iterrows():
                                    with st.container():
                                        col_img, col_info = st.columns([1, 3])
                                        with col_img:
                                            img = event.get('image')
                                            if pd.notna(img) and img:
                                                try:
                                                    st.image(img, use_container_width=True)
                                                except Exception:
                                                    st.info("📷 이미지 없음")
                                            else:
                                                st.info("📷 이미지 없음")
                                        with col_info:
                                            st.markdown(f"**{event['title']}**")
                                            if HAS_BENEFIT:
                                                bc = st.columns(3)
                                                with bc[0]:
                                                    st.markdown(f"**💰 {int(event.get('benefit_amt', 0)):,}원**")
                                                with bc[1]:
                                                    emoji_map = {"VOUCHER": "🎁", "CASHBACK": "💵", "DISCOUNT": "🔖", "POINT": "⭐", "ERROR": "❌"}
                                                    bt = event.get('benefit_type', 'UNKNOWN')
                                                    st.markdown(f"**{emoji_map.get(bt, '❓')} {bt}**")
                                                with bc[2]:
                                                    st.markdown(f"**🕐 {event.get('time_str', 'N/A')}**")
                                            summary = event.get('ai_summary') or event.get('notice', '')
                                            if pd.notna(summary) and str(summary) not in ['분석 실패', 'nan', '']:
                                                st.markdown(f"_{str(summary)[:100]}_")
                                            with st.expander("상세 정보"):
                                                notice = event.get('notice', 'N/A')
                                                st.markdown(f"- 유의사항: {str(notice)[:200] if pd.notna(notice) else 'N/A'}")
                                                st.markdown(f"- [이벤트 페이지 바로가기]({event.get('url', '#')})")
                                    st.markdown("---")

                    if round_idx < len(rounds) - 1:
                        st.divider()

# ─────────────────────────────────────────────────────────
# Tab 2: 전체 레코드
# ─────────────────────────────────────────────────────────
with tab2:
    sort_c1, sort_c2 = st.columns([3, 1])
    with sort_c1:
        sort_options = ["최신순", "회사별", "제목순"]
        if HAS_BENEFIT:
            sort_options.insert(1, "혜택 금액 높은순")
        sort_by = st.selectbox("정렬 기준", sort_options)
    with sort_c2:
        st.metric("현재 레코드", f"{len(filtered_df):,}건")

    display_df = filtered_df.copy()
    if sort_by == "최신순":
        display_df = display_df.sort_values('date', ascending=False)
    elif sort_by == "혜택 금액 높은순" and HAS_BENEFIT:
        display_df = display_df.sort_values('benefit_amt', ascending=False)
    elif sort_by == "회사별":
        display_df = display_df.sort_values(['company', 'date'], ascending=[True, False])
    else:
        display_df = display_df.sort_values('title')

    # 컬럼 설정 동적 구성
    col_config_t2 = {
        "crawl_round": st.column_config.TextColumn("수집 회차", width="medium"),
        "date": st.column_config.DatetimeColumn("수집 일시", format="MM-DD HH:mm", width="small"),
        "company": st.column_config.TextColumn("통신사", width="small"),
        "title": st.column_config.TextColumn("이벤트명", width="large"),
        "url": st.column_config.LinkColumn("링크", width="small"),
        "image": None,
        "date_only": None,
        "time_str": None,
        "main_content": None,
        "notice": None,
    }
    if HAS_BENEFIT:
        col_config_t2["benefit_amt"] = st.column_config.NumberColumn("혜택", format="%d원", width="small")
        col_config_t2["benefit_type"] = st.column_config.TextColumn("유형", width="small")
        col_config_t2["cond_type"] = st.column_config.TextColumn("조건", width="small")
        col_config_t2["ai_summary"] = st.column_config.TextColumn("AI 요약", width="medium")
        col_config_t2["cond_plan_price"] = None

    st.dataframe(
        display_df,
        column_config=col_config_t2,
        hide_index=True,
        use_container_width=True,
        height=600
    )

    csv = display_df.to_csv(index=False, encoding='utf-8-sig')
    st.download_button(
        "📥 현재 조회 결과 CSV 다운로드",
        csv,
        f"event_history_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        "text/csv"
    )

# ─────────────────────────────────────────────────────────
# Tab 3: 통계 분석
# ─────────────────────────────────────────────────────────
with tab3:
    st.subheader("📊 데이터 분석")

    st.markdown("#### 📈 회차별 신규 vs 종료 이벤트")
    st.caption("회차마다 새로 생긴 이벤트와 사라진 이벤트 수")
    try:
        rounds_sorted = sorted(filtered_df['crawl_round'].unique())
        new_end_rows = []
        prev_urls = set()

        for round_str in rounds_sorted:
            curr_urls = set(filtered_df[filtered_df['crawl_round'] == round_str]['url'].values)
            new_cnt = len(curr_urls - prev_urls) if prev_urls else 0
            end_cnt = len(prev_urls - curr_urls) if prev_urls else 0
            new_end_rows.append({"회차": round_str, "신규": new_cnt, "종료": -end_cnt})
            prev_urls = curr_urls

        df_new_end = pd.DataFrame(new_end_rows[1:])

        if not df_new_end.empty:
            df_melted = df_new_end.melt(id_vars="회차", value_vars=["신규", "종료"], var_name="구분", value_name="건수")
            chart1 = alt.Chart(df_melted).mark_bar().encode(
                x=alt.X('회차:N', title='수집 회차', axis=alt.Axis(labelAngle=-30)),
                y=alt.Y('건수:Q', title='이벤트 수'),
                color=alt.Color('구분:N', scale=alt.Scale(domain=['신규', '종료'], range=['#2ecc71', '#e74c3c'])),
                tooltip=['회차:N', '구분:N', '건수:Q']
            ).properties(height=300)
            st.altair_chart(chart1, use_container_width=True)
        else:
            st.info("📊 2회차 이상 수집 후 표시됩니다.")
    except Exception as e:
        st.warning(f"차트 생성 오류: {str(e)[:100]}")

    if HAS_BENEFIT:
        st.divider()
        st.markdown("#### 💰 혜택 금액 구간별 분포")
        try:
            df_benefit = filtered_df[filtered_df['benefit_amt'] > 0].copy()
            if not df_benefit.empty:
                bins = [0, 10000, 30000, 50000, 100000, float('inf')]
                labels = ['1만원 미만', '1~3만원', '3~5만원', '5~10만원', '10만원 이상']
                df_benefit['구간'] = pd.cut(df_benefit['benefit_amt'], bins=bins, labels=labels)
                bucket_counts = df_benefit.groupby(['구간', 'company'], observed=True).size().reset_index(name='건수')
                chart2 = alt.Chart(bucket_counts).mark_bar().encode(
                    x=alt.X('구간:N', title='혜택 금액 구간', sort=labels),
                    y=alt.Y('건수:Q', title='이벤트 수'),
                    color=alt.Color('company:N', title='통신사'),
                    tooltip=['구간:N', 'company:N', '건수:Q']
                ).properties(height=300)
                st.altair_chart(chart2, use_container_width=True)
        except Exception as e:
            st.warning(f"차트 생성 오류: {str(e)[:100]}")

    st.divider()
    st.markdown("#### ⏳ 통신사별 이벤트 평균 생존 기간")
    try:
        survival = filtered_df.groupby(['url', 'company']).agg(
            first_seen=('date', 'min'),
            last_seen=('date', 'max')
        ).reset_index()
        survival['생존일수'] = (survival['last_seen'] - survival['first_seen']).dt.days
        survival = survival[survival['생존일수'] > 0]

        if not survival.empty:
            avg_survival = survival.groupby('company')['생존일수'].mean().reset_index()
            avg_survival.columns = ['통신사', '평균 생존일수']
            avg_survival = avg_survival.sort_values('평균 생존일수', ascending=False)
            chart3 = alt.Chart(avg_survival).mark_bar().encode(
                x=alt.X('평균 생존일수:Q', title='평균 생존일수 (일)'),
                y=alt.Y('통신사:N', sort='-x'),
                color=alt.Color('통신사:N', legend=None, scale=alt.Scale(scheme='category10')),
                tooltip=[alt.Tooltip('통신사:N'), alt.Tooltip('평균 생존일수:Q', format='.1f')]
            ).properties(height=300)
            st.altair_chart(chart3, use_container_width=True)
        else:
            st.info("📊 생존 기간 분석에는 동일 이벤트가 2회 이상 수집되어야 합니다.")
    except Exception as e:
        st.warning(f"차트 생성 오류: {str(e)[:100]}")

    st.divider()
    st.markdown("#### 🔥 가장 오래 지속된 이벤트 TOP 10")
    event_longevity = filtered_df.groupby('url').agg(
        제목=('title', 'first'),
        통신사=('company', 'first'),
        수집횟수=('crawl_round', 'nunique'),
        최초수집=('date', 'min'),
        최근수집=('date', 'max')
    ).sort_values('수집횟수', ascending=False).head(10)
    st.dataframe(event_longevity, use_container_width=True)

# ─────────────────────────────────────────────────────────
# Tab 4: 이벤트 추적
# ─────────────────────────────────────────────────────────
with tab4:
    st.caption("특정 이벤트가 언제부터 있었는지 추적")

    search_keyword = st.text_input("제목 키워드 검색", placeholder="예: 친구추천, 사전예약, 갤럭시")

    if search_keyword:
        matching = df[df['title'].str.contains(search_keyword, case=False, na=False)]

        if matching.empty:
            st.warning("검색 결과가 없습니다.")
        else:
            event_options = matching[['title', 'company', 'url']].drop_duplicates('url')
            selected_idx = st.selectbox(
                f"이벤트 선택 ({len(event_options)}개 검색됨)",
                range(len(event_options)),
                format_func=lambda x: f"{event_options.iloc[x]['company']} — {event_options.iloc[x]['title'][:50]}"
            )
            selected_url = event_options.iloc[selected_idx]['url']
            event_history = df[df['url'] == selected_url].sort_values('date', ascending=False)

            st.success(f"총 **{len(event_history)}회** 수집된 이벤트")

            m1, m2, m3, m4 = st.columns(4)
            with m1: st.metric("수집 횟수", f"{len(event_history)}회")
            with m2: st.metric("최초 발견", event_history['date'].min().strftime('%m/%d'))
            with m3: st.metric("최근 수집", event_history['date'].max().strftime('%m/%d'))
            with m4:
                days = (event_history['date'].max() - event_history['date'].min()).days
                st.metric("생존 기간", f"{days}일")

            if HAS_BENEFIT and len(event_history) > 1 and event_history['benefit_amt'].max() > 0:
                st.markdown("**💰 혜택 금액 변화 추이**")
                chart_track = alt.Chart(event_history).mark_line(point=True).encode(
                    x=alt.X('date:T', title='수집 일시'),
                    y=alt.Y('benefit_amt:Q', title='혜택 금액 (원)'),
                    tooltip=[
                        alt.Tooltip('date:T', title='수집 일시', format='%Y-%m-%d %H:%M'),
                        alt.Tooltip('benefit_amt:Q', title='혜택 금액', format=',')
                    ]
                ).properties(height=200)
                st.altair_chart(chart_track, use_container_width=True)

            st.markdown("**📋 전체 수집 이력**")
            history_cols = ['crawl_round']
            history_col_config = {
                "crawl_round": st.column_config.TextColumn("수집 회차", width="medium"),
            }
            for c, cfg in [
                ('benefit_amt', st.column_config.NumberColumn("혜택", format="%d원", width="small")),
                ('benefit_type', st.column_config.TextColumn("유형", width="small")),
                ('ai_summary', st.column_config.TextColumn("AI 요약", width="large")),
                ('notice', st.column_config.TextColumn("유의사항", width="large")),
            ]:
                if c in event_history.columns:
                    history_cols.append(c)
                    history_col_config[c] = cfg

            st.dataframe(
                event_history[history_cols],
                column_config=history_col_config,
                hide_index=True,
                use_container_width=True
            )
    else:
        st.info("👆 키워드를 입력하면 해당 이벤트의 전체 수집 이력을 추적할 수 있습니다.")
