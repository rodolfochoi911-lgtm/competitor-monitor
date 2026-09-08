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
    for field in ('title', 'img', 'main_content'):
        before, after = old.get(field, '') or '', new.get(field, '') or ''
        if (before != after if field == 'img' else clean_text(before) != clean_text(after)):
            changes[field] = {'old': before, 'new': after}
    return changes


def notice_lines(value):
    soup = BeautifulSoup(value or '', 'html.parser')
    for tag in soup(['script', 'style', 'noscript']):
        tag.decompose()
    footer = {'이용약관', '개인정보처리방침', '개인정보 처리방침', '로그인', '회원가입'}
    return {re.sub(r'\s+', ' ', line).strip()
            for line in soup.get_text('\n', strip=True).splitlines()
            if line.strip() and line.strip() not in footer}


def calculate_notice_diff(current, previous):
    result = {}
    for company in sorted(set(current) | set(previous)):
        curr, prev = current.get(company, {}), previous.get(company, {})
        added, removed = [], []
        for url in sorted(set(curr) | set(prev)):
            old, new = prev.get(url, {}), curr.get(url, {})
            before, after = notice_lines(old.get('notice')), notice_lines(new.get('notice'))
            title = new.get('title') or old.get('title') or company
            label = f'[{title}] ({url}) '
            added.extend(label + line for line in sorted(after - before))
            removed.extend(label + line for line in sorted(before - after))
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
