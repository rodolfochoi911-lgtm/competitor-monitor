"""Shared change detection for notifications and the dashboard."""
import re
import copy
import json
from pathlib import Path
from bs4 import BeautifulSoup


def clean_text(value):
    soup = BeautifulSoup(value or '', 'html.parser')
    for tag in soup(['script', 'style', 'noscript']):
        tag.decompose()
    return re.sub(r'\s+', ' ', soup.get_text(' ', strip=True)).strip()


BODY_NOISE_RE = re.compile(
    r'^(?:조회\s*수\s*[:：]?\s*\d+|'
    r'(?:검색일|수집일|크롤링\s*시각|최종\s*수정일)\s*[:：]?.*)$',
    re.IGNORECASE,
)


def normalized_body(value):
    soup = BeautifulSoup(value or '', 'html.parser')
    for tag in soup(['script', 'style', 'noscript']):
        tag.decompose()
    lines = [re.sub(r'\s+', ' ', line).strip()
             for line in soup.get_text('\n', strip=True).splitlines()]
    return ' '.join(line for line in lines if line and not BODY_NOISE_RE.match(line))


def detect_changes(old, new):
    changes = {}
    for field in ('title', 'img', 'main_content'):
        before, after = old.get(field, '') or '', new.get(field, '') or ''
        if field == 'img':
            changed = before != after
        elif field == 'main_content':
            # Avoid alerting on a temporary empty extraction.
            changed = bool(before and after) and normalized_body(before) != normalized_body(after)
        else:
            changed = clean_text(before) != clean_text(after)
        if changed:
            changes[field] = {'old': before, 'new': after}

    # Establish baseline on first deployment, rather than alerting for every old event.
    before_images = old.get('detail_image_hashes')
    after_images = new.get('detail_image_hashes')
    if before_images is not None and after_images is not None and before_images != after_images:
        changes['detail_image_hashes'] = {'old': before_images, 'new': after_images}
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


PERIOD_NOTICE_RE = re.compile(
    r'20\d{2}\s*(?:[./-]|년)\s*\d{1,2}|'
    r'\d{1,2}\s*월\s*\d{1,2}\s*일|'
    r'\d{1,2}\s*[./-]\s*\d{1,2}|'
    r'\d+\s*(?:영업일|개월|달|주|일|시간)\s*(?:간|이내|전|후|동안)?|'
    r'기간|기한|선정일|발송일|지급일|사용일|적용일|신청일|종료일'
)
NOTICE_NOISE_RE = re.compile(
    r'^(?:조회\s*수|검색일|수집일|크롤링\s*시각|최종\s*수정일)',
    re.IGNORECASE,
)


def notice_lines(value):
    """금액/혜택 및 날짜·기간·기한 관련 유의사항을 반환한다."""
    soup = BeautifulSoup(value or '', 'html.parser')
    for tag in soup(['script', 'style', 'noscript']):
        tag.decompose()
    footer = {'이용약관', '개인정보처리방침', '개인정보 처리방침', '로그인', '회원가입'}
    lines = set()
    for raw_line in soup.get_text('\n', strip=True).splitlines():
        line = re.sub(r'\s+', ' ', raw_line).strip()
        if not line or line in footer or NOTICE_NOISE_RE.search(line):
            continue
        if (AMOUNT_NOTICE_RE.search(line) or PERCENT_BENEFIT_RE.search(line)
                or PERIOD_NOTICE_RE.search(line)):
            lines.add(line)
    return lines


def company_notice_sources(data, company):
    """Keep original event URL/title for Slack while deduplicating company-wide."""
    sources = {}
    for url, event in data.get(company, {}).items():
        for line in notice_lines(event.get('notice')):
            sources.setdefault(line, {'url': url, 'title': event.get('title', '')})
    return sources


def company_notice_lines(data, company):
    return set(company_notice_sources(data, company))


def calculate_notice_diff(current, previous):
    current, previous = comparison_snapshots(current, previous)
    result = {}
    for company in sorted(set(current) | set(previous)):
        before_sources = company_notice_sources(previous, company)
        after_sources = company_notice_sources(current, company)
        added = sorted(after_sources.keys() - before_sources.keys())
        removed = sorted(before_sources.keys() - after_sources.keys())
        if added or removed:
            result[company] = {
                'added': added, 'removed': removed,
                'total': len(added) + len(removed),
                'added_sources': {line: after_sources[line] for line in added},
                'removed_sources': {line: before_sources[line] for line in removed},
            }
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
            for field in ('main_content', 'notice', 'detail_image_hashes'):
                if field == 'detail_image_hashes':
                    missing = old.get(field) is not None and event.get(field) is None
                else:
                    missing = bool(clean_text(old.get(field))) and not clean_text(event.get(field))
                if missing:
                    event[field] = old[field]
                    retained[field] = old.get('_retained_fields', {}).get(field) or previous_snapshot
            if retained:
                event['_retained_fields'] = retained
                event['full_text'] = '\n\n'.join(filter(None, [event.get('main_content'), event.get('notice')]))
    return collection_warnings(current)


def collection_warnings(data):
    labels = {'main_content': '본문', 'notice': '유의사항', 'detail_image_hashes': '상세 이미지'}
    warnings = []
    for company, events in sorted(data.items()):
        if any(event.get('_collection_error') for event in events.values()):
            warnings.append(f'{company}: 수집 실패로 이번 결과 및 변경 집계에서 제외 (이전 데이터는 비교 기준으로 보관)')
            continue
        for url, event in sorted(events.items()):
            retained = event.get('_retained_fields', {})
            if retained:
                fields = ', '.join(labels.get(field, field) for field in retained)
                warnings.append(f"{company} / {event.get('title', url)}: {fields} 재확인 필요, 이전 수집값 보존 ({url})")
    return warnings


def successful_snapshot(data):
    """Exclude retained company baselines from fresh observations."""
    return {company: events for company, events in data.items()
            if events and not any(event.get('_collection_error') for event in events.values())}


def comparison_snapshots(current, previous):
    current = successful_snapshot(current)
    return current, {company: events for company, events in previous.items() if company in current}


def prepare_partial_snapshot(results, previous, errors):
    """Publish successful companies, carrying failed baselines for recovery only."""
    successful = {company: events for company, events in results.items() if events and company not in errors}
    if not successful:
        raise RuntimeError('모든 회사 수집 실패: 이전 정상 데이터를 유지합니다.')
    snapshot = copy.deepcopy(successful)
    for company, events in previous.items():
        if company not in snapshot and events:
            snapshot[company] = copy.deepcopy(events)
            for event in snapshot[company].values():
                event['_collection_error'] = errors.get(company, '수집 결과 누락')
    validate_snapshot(snapshot, previous)
    return snapshot


def snapshot_warnings(data, snapshot_path):
    warnings = collection_warnings(data)
    status_path = Path(snapshot_path).with_name(Path(snapshot_path).name.replace('data_', 'collection_status_', 1))
    if status_path.exists():
        errors = json.loads(status_path.read_text(encoding='utf-8'))
        for company, error in sorted(errors.items()):
            if company not in data:
                warnings.append(f'{company}: 수집 실패로 이번 결과에서 제외 ({error})')
    return warnings

