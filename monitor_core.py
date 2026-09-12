"""Shared change detection for notifications and the dashboard."""
import re
from bs4 import BeautifulSoup


def clean_text(value):
    soup = BeautifulSoup(value or '', 'html.parser')
    for tag in soup(['script', 'style', 'noscript']):
        tag.decompose()
    return re.sub(r'\s+', ' ', soup.get_text(' ', strip=True)).strip()


def detect_changes(old, new):
    changes = {}
    # 본문에는 조회수, 검색일 등 수집 때마다 달라지는 값이 섞인다.
    # 행사 자체의 수정 판정은 안정적인 제목과 이미지만 사용한다.
    for field in ('title', 'img'):
        before, after = old.get(field, '') or '', new.get(field, '') or ''
        if (before != after if field == 'img' else clean_text(before) != clean_text(after)):
            changes[field] = {'old': before, 'new': after}
    return changes


AMOUNT_NOTICE_RE = re.compile(
    r'(?:\d[\d,]*(?:\.\d+)?\s*(?:억|만|천)?\s*원|'
    r'\d[\d,]*(?:\.\d+)?\s*(?:억|만|천)?\s*(?:포인트|point|points|p)\b)',
    re.IGNORECASE,
)
PERCENT_BENEFIT_RE = re.compile(
    r'\d+(?:\.\d+)?\s*%.*(?:할인|적립|캐시백|환급)|'
    r'(?:할인|적립|캐시백|환급).*\d+(?:\.\d+)?\s*%'
)


def notice_lines(value):
    """금액 혜택과 조건에 관계된 유의사항만 반환한다."""
    soup = BeautifulSoup(value or '', 'html.parser')
    for tag in soup(['script', 'style', 'noscript']):
        tag.decompose()
    footer = {'이용약관', '개인정보처리방침', '개인정보 처리방침', '로그인', '회원가입'}
    lines = set()
    for raw_line in soup.get_text('\n', strip=True).splitlines():
        line = re.sub(r'\s+', ' ', raw_line).strip()
        if not line or line in footer:
            continue
        if AMOUNT_NOTICE_RE.search(line) or PERCENT_BENEFIT_RE.search(line):
            lines.add(line)
    return lines


def company_notice_lines(data, company):
    """이벤트 경계를 없애고 회사별 금액 유의사항을 하나로 합친다."""
    result = set()
    for event in data.get(company, {}).values():
        result.update(notice_lines(event.get('notice')))
    return result


def calculate_notice_diff(current, previous):
    result = {}
    for company in sorted(set(current) | set(previous)):
        before = company_notice_lines(previous, company)
        after = company_notice_lines(current, company)
        added = sorted(after - before)
        removed = sorted(before - after)
        if added or removed:
            result[company] = {'added': added, 'removed': removed,
                               'total': len(added) + len(removed)}
    return result


def validate_snapshot(current, previous=None):
    """Reject empty/missing company collections before interpreting event termination."""
    if not current or any(not events for events in current.values()):
        raise RuntimeError('수집 결과가 비어 있습니다. 정상 데이터와 종료 판정을 보존합니다.')
    for company, events in (previous or {}).items():
        if company not in current:
            raise RuntimeError(f'{company}: 수집 결과 누락')


def preserve_unavailable_fields(current, previous, previous_snapshot=''):
    """Keep last known text on transient empty extraction, with explicit provenance.

    This is not a successful observation of that field. A recovered nonempty value
    clears the marker naturally because the crawler constructs fresh event records.
    Missing companies still fail validation; missing URLs are not restored here.
    """
    validate_snapshot(current, previous)
    for company, events in current.items():
        for url, event in events.items():
            old = previous.get(company, {}).get(url, {})
            retained = {}
            for field in ('main_content', 'notice'):
                if clean_text(old.get(field)) and not clean_text(event.get(field)):
                    event[field] = old[field]
                    retained[field] = old.get('_retained_fields', {}).get(field) or previous_snapshot
            if retained:
                event['_retained_fields'] = retained
                event['full_text'] = '\n\n'.join(filter(None, [event.get('main_content'), event.get('notice')]))
    return collection_warnings(current)


def collection_warnings(data):
    labels = {'main_content': '본문', 'notice': '유의사항'}
    warnings = []
    for company, events in sorted(data.items()):
        for url, event in sorted(events.items()):
            retained = event.get('_retained_fields', {})
            if retained:
                fields = ', '.join(labels.get(field, field) for field in retained)
                warnings.append(f"{company} / {event.get('title', url)}: {fields} 재확인 필요, 이전 수집값 보존 ({url})")
    return warnings

