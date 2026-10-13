"""Remove live counters, never event dates or contractual durations."""
import re
from bs4 import BeautifulSoup, Comment

COUNTERS = re.compile(
    r'(?<![\w])(?:조회\s*수?|views?)\s*[:：]?\s*[\d,]+\s*(?:회|건)?'
    r'|(?<![\w])D\s*[-−–+]\s*\d+\b|(?<![\w])D\s*-?\s*DAY\b'
    r'|\d+\s*일\s*(?:남음|남았습니다)', re.I)
# Timer text can contain a D-day number and a separate HH:MM clock.
# Remove the entire live countdown, keeping actual event date ranges intact.
LIVE_COUNTDOWN = re.compile(
    r'(?<![\w])(?:이벤트\s*|행사\s*|프로모션\s*)?(?:마감|종료|시작|오픈)\s*까지\s*'
    r'D\s*[-−–]\s*\d+(?:\s*일)?(?:\s+\d{1,2}\s*[:：]\s*\d{2}(?:\s*[:：]\s*\d{2})?)?'
    r'\s*(?:남았어요!?|남았습니다!?|남음)?'
    r'|(?<![\w])D\s*[-−–]\s*\d+(?:\s*일)?\s+\d{1,2}\s*[:：]\s*\d{2}'
    r'(?:\s*[:：]\s*\d{2})?\s*(?:남았어요!?|남았습니다!?|남음)?',
    re.I,
)
METADATA = re.compile(r'^(?:검색일|수집일|크롤링\s*시각|최종\s*수정일)\s*[:：]?.*$', re.I)
COUNTER_SELECTORS = ('.d-day, .dday, .dDay, .day-count, .countdown, .view-count, .views, '
                     '[class*="countdown" i], [id*="countdown" i], '
                     '[class*="dday" i], [id*="dday" i]')


def strip_counters(text):
    text = LIVE_COUNTDOWN.sub('', text or '')
    return re.sub(r'[ \t]+', ' ', COUNTERS.sub('', text)).strip()


def stable_text(value):
    soup = BeautifulSoup(value or '', 'html.parser')
    for tag in soup.select('script, style, noscript, ' + COUNTER_SELECTORS):
        tag.decompose()
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    raw_text = LIVE_COUNTDOWN.sub('', soup.get_text('\n', strip=True))
    lines = [strip_counters(line) for line in raw_text.splitlines()]
    # Support metadata split into label and number by inline markup.
    result, skip_number = [], False
    for line in lines:
        if re.fullmatch(r'(?:조회\s*수?|views?)\s*[:：]?', line, re.I):
            skip_number = True
            continue
        if skip_number and re.fullmatch(r'[\d,]+\s*(?:회|건)?', line):
            skip_number = False
            continue
        skip_number = False
        if line and not METADATA.fullmatch(line):
            result.append(line)
    return '\n'.join(result)
