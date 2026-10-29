"""Read campaign copy within verified site boundaries, independent of CSS state."""
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from notice_regions import region_text
from promotion_noise import stable_text

SCOPES = {
    'www.ktmmobile.com': '.c-board__content',
    'direct.lghellovision.net': '.boardView .substance',
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
    remove = ['script', 'style', 'noscript', 'template', 'header', 'footer', 'nav',
              '[role="dialog"]', '[aria-modal="true"]', '.pageGo', '#prevNext']
    if host == 'www.ktmmobile.com':
        # These named modules are site-wide banners, purchase navigation,
        # partner cards and related events. PRMT0007 contains real terms.
        remove += ['#PRMT0001', '#PRMT0015', '#PRMT0019', '#PRMT0017', '#PRMT0023',
                   '.attention-box']
    else:
        remove += ['.noticeWrap', '.viewNotice']
    for node in root.select(','.join(remove)):
        node.decompose()
    # Hidden rate-benefit variants are part of the campaign. Do not discard
    # them or deduplicate repeated amounts: that loses each rate's terms.
    text = stable_text(region_text(root))
    image_only = bool(root.select('img[src], img[data-src]')) and not text
    return {'main_content': text, '_content_version': 'campaign-dom-v1',
            '_body_status': 'image_only' if image_only else ('text' if text else 'unavailable')}
