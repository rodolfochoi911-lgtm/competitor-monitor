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
    """Never interpret an unverified empty or partial crawl as event termination."""
    if not current or any(not events for events in current.values()):
        raise RuntimeError('수집 결과가 비어 있습니다. 정상 데이터와 종료 판정을 보존합니다.')
    for company, events in (previous or {}).items():
        if company not in current:
            raise RuntimeError(f'{company}: 수집 결과 누락')
        for url in set(events) & set(current[company]):
            old, new = events[url], current[company][url]
            for field in ('main_content', 'notice'):
                if clean_text(old.get(field)) and not clean_text(new.get(field)):
                    raise RuntimeError(f'{company}: {field} 추출 누락 ({url})')
