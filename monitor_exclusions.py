"""Explicit low-value events excluded at the user's request on 2026-11-02.

Keep this list independent of extraction status: new collection failures still warn.
Historical snapshots remain unchanged on disk.
"""
from urllib.parse import parse_qs, urlsplit


EXCLUDED_EVENT_URLS = (
    'https://shop.tworld.co.kr/nf/index_nf_yp_plan.html?exhibitionId=P00000513',
    'https://shop.tworld.co.kr/nf/index_nf_yp_plan.html?exhibitionId=P00000480',
    'https://shop.tworld.co.kr/nf/index_nf_yp_plan.html?exhibitionId=P00000344',
    'https://shop.tworld.co.kr/nf/index_nf_yp_plan.html?exhibitionId=P00000282',
    'https://shop.tworld.co.kr/nf/index_nf_yp_plan.html?exhibitionId=P00000265',
    'https://www.ktmmobile.com/event/eventDetail.do?ntcartSeq=1852',
    'https://www.ktmmobile.com/event/eventDetail.do?ntcartSeq=1417',
    'https://www.ktmmobile.com/event/eventDetail.do?ntcartSeq=948',
    'https://www.ktmmobile.com/event/eventDetail.do?ntcartSeq=721',
    'https://www.uplusumobile.com/event-benefit/event/ongoing/12641',
    'https://www.uplusumobile.com/event-benefit/event/ongoing/12627',
    'https://www.uplusumobile.com/event-benefit/event/ongoing/12380',
    'https://www.uplusumobile.com/event-benefit/event/ongoing/10411',
    'https://www.skylife.co.kr/event/roaming.do',
    'https://www.skylife.co.kr/event/self5m.do',
    'https://www.skylife.co.kr/event/mobile_cs.do',
    'https://www.skylife.co.kr/event/001.do',
    'https://direct.lghellovision.net/event/viewEventDetailGuest.do?idxOfEvent=5551',
    'https://npay.sk7mobile.com/main.do',
)


def event_key(url):
    parts = urlsplit(url)
    host = (parts.hostname or '').lower()
    path = parts.path.rstrip('/')
    if host == 'shop.tworld.co.kr' and path == '/exhibition/view':
        path = '/nf/index_nf_yp_plan.html'
    identity_param = {
        ('shop.tworld.co.kr', '/nf/index_nf_yp_plan.html'): 'exhibitionId',
        ('www.ktmmobile.com', '/event/eventDetail.do'): 'ntcartSeq',
        ('direct.lghellovision.net', '/event/viewEventDetailGuest.do'): 'idxOfEvent',
    }.get((host, path))
    query = parse_qs(parts.query)
    identity = tuple(query.get(identity_param, ())) if identity_param else ()
    return host, path, identity


EXCLUDED_EVENT_KEYS = frozenset(event_key(url) for url in EXCLUDED_EVENT_URLS)


def is_excluded_event(url):
    """Ignore tracking parameters while retaining the event's identity."""
    return event_key(url) in EXCLUDED_EVENT_KEYS


def filter_snapshot(data):
    """Filter both comparison sides so exclusion never looks like termination."""
    return {company: {url: event for url, event in events.items()
                      if not is_excluded_event(url)}
            for company, events in data.items()}
