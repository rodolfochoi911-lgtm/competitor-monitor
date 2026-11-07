"""Read campaign copy within verified site boundaries, independent of CSS state."""
from urllib.parse import urlsplit
import re
from bs4 import BeautifulSoup
from notice_regions import region_text
from promotion_noise import stable_text

SCOPES = {
    'www.ktmmobile.com': '.c-board__content',
    'direct.lghellovision.net': '.boardView .substance',
    'shop.tworld.co.kr': '.event-detail-content',
    'www.uplusumobile.com': 'main .sub-conts.template',
}


def extract_campaign_body(html, url):
    host = (urlsplit(url).hostname or '').lower()
    selector = SCOPES.get(host)
    if selector is None:
        return None
    soup = BeautifulSoup(html, 'html.parser')
    root = soup.select_one(selector)
    if root is None:
        raise RuntimeError(f'이벤트 본문 영역 없음: {selector}')
    metadata = {}
    if host == 'www.uplusumobile.com':
        heading = root.select_one('.template-tit')
        if heading is not None:
            dates = re.findall(r'20\d{2}-\d{2}-\d{2}', heading.get_text(' ', strip=True))
            if dates:
                metadata['event_period'] = ' ~ '.join(dates[:2])
    remove = ['script', 'style', 'noscript', 'template', 'header', 'footer', 'nav',
              '[role="dialog"]', '[aria-modal="true"]', '.pageGo', '#prevNext']
    if host == 'www.ktmmobile.com':
        # These named modules are site-wide banners, purchase navigation,
        # partner cards and related events. PRMT0007 contains real terms.
        remove += ['#PRMT0001', '#PRMT0015', '#PRMT0019', '#PRMT0017', '#PRMT0023',
                   '.attention-box']
    elif host == 'direct.lghellovision.net':
        remove += ['.noticeWrap', '.viewNotice']
    elif host == 'shop.tworld.co.kr':
        # Terms have their own verified channel; the body must not depend on
        # accordion visibility or include personalized discount dialogs.
        remove += ['.arcBoxWrap', '#trgtSupmOfferPop', '.modal', '.layer-popup',
                   '.share', '.sns-share']
    elif host == 'www.uplusumobile.com':
        # info/blind classes also contain real prices and image descriptions.
        # Keep those; remove only identified official notice/recommendation sections.
        for section in list(root.select('section.template-info.unit-origin')):
            headings = section.select('h2,h3,.fix-info-tit')
            if any('유의사항' in h.get_text() or '다른 이벤트' in h.get_text() for h in headings):
                section.decompose()
        remove += ['.template-tit', '.template-view.sticky-title', '.modal', '.popup',
                   '.pop-wrap', '.layer-popup', '.evt-strip']
    for node in root.select(','.join(remove)):
        node.decompose()
    # Hidden rate-benefit variants are part of the campaign. Do not discard
    # them or deduplicate repeated amounts: that loses each rate's terms.
    text = stable_text(region_text(root))
    if host == 'www.uplusumobile.com':
        text = '\n'.join(line for line in text.splitlines()
                         if line not in {'진행중인 이벤트', '공유아이콘', '다른 이벤트도 살펴보세요.'})
    image_only = bool(root.select('img[src], img[data-src]')) and not text
    return {**metadata, 'main_content': text, '_content_version': 'campaign-dom-v1',
            '_body_status': 'image_only' if image_only else ('text' if text else 'unavailable')}
