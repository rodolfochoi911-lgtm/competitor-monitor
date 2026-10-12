"""Remove live counters, never event dates or contractual durations."""
import re
from bs4 import BeautifulSoup, Comment

COUNTERS = re.compile(
    r'(?<![\w])(?:조회\s*수?|views?)\s*[:：]?\s*[\d,]+\s*(?:회|건)?'
    r'|(?<![\w])D\s*[-−–+]\s*\d+\b|(?<![\w])D\s*-?\s*DAY\b'
    r'|\d+\s*일\s*(?:남음|남았습니다)', re.I)
METADATA = re.compile(r'^(?:검색일|수집일|크롤링\s*시각|최종\s*수정일)\s*[:：]?.*$', re.I)
COUNTER_SELECTORS = '.d-day, .dday, .dDay, .day-count, .countdown, .view-count, .views'


def strip_counters(text):
    return re.sub(r'[ \t]+', ' ', COUNTERS.sub('', text or '')).strip()


def stable_text(value):
    soup = BeautifulSoup(value or '', 'html.parser')
    for tag in soup.select('script, style, noscript, ' + COUNTER_SELECTORS):
        tag.decompose()
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    lines = [strip_counters(line) for line in soup.get_text('\n', strip=True).splitlines()]
    # Support metadata split into label and number by inline markup.
    result, skip_number = [], False
    for line in lines:
        if re.fullmatch(r'조회\s*수?|views?', line, re.I):
            skip_number = True
            continue
        if skip_number and re.fullmatch(r'[\d,]+\s*(?:회|건)?', line):
            skip_number = False
            continue
        skip_number = False
        if line and not METADATA.fullmatch(line):
            result.append(line)
    return '\n'.join(result)
