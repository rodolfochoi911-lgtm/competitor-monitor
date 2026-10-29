"""Wait for hydrated campaign accordions and read their final DOM once."""
import time
from bs4 import BeautifulSoup
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException
from notice_regions import region_text
from promotion_noise import stable_text, is_error_title


def campaign_root(soup):
    iframe = soup.select_one('iframe#iframe-promotion')
    if iframe is None:
        return None
    for parent in iframe.parents:
        if parent.name in ('body', 'html'):
            break
        if parent.select('button[data-radix-collection-item][aria-controls]'):
            return parent
    return None


def read_skylife_dom(html):
    soup = BeautifulSoup(html, 'html.parser')
    for node in soup.select('script,style,noscript'):
        node.decompose()
    if is_error_title(soup.get_text(' ', strip=True)):
        raise RuntimeError('스카이라이프 오류 화면 응답')
    iframe = soup.select_one('iframe#iframe-promotion')
    root = campaign_root(soup)
    if iframe is None or not iframe.get('srcdoc'):
        return {'_detail_ready': False, '_notice_complete': False, 'notice': '',
                'main_content': '', '_notice_missing_regions': ['event-not-loaded']}
    inner = BeautifulSoup(iframe['srcdoc'], 'html.parser')
    for node in inner.select('script,style,noscript'):
        node.decompose()
    body = stable_text(region_text(inner))
    targets, missing = [], []
    controls = root.select('button[data-radix-collection-item][aria-controls]') if root is not None else []
    if not controls:
        missing.append('campaign-accordion-not-present')
    for control in controls:
        for target in control.get('aria-controls', '').split():
            panel = root.find(id=target)
            if (panel is None or panel.get('data-state') != 'open'
                    or control.get('data-state') != 'open' or not region_text(panel)):
                missing.append(target)
            if panel is not None:
                targets.append((control, panel))
    # The outer panel already contains all nested panels. Read its final tree
    # once; never accumulate parent snapshots before/after each child opens.
    outer = [(c, p) for c, p in targets if not any(other is not p and other in p.parents for _, other in targets)]
    regions = []
    for i, (control, panel) in enumerate(outer):
        content = BeautifulSoup(str(panel), 'html.parser')
        for heading in content.select('button[data-radix-collection-item]'):
            heading.name = 'strong'  # Nested section names are contractual context.
        regions.append({'id': str(i), 'title': control.get_text(' ', strip=True),
                        'text': stable_text(region_text(content))})
    notice = '\n'.join('[' + r['title'] + ']\n' + r['text'] for r in regions if r['text'])
    return {'_detail_ready': True, 'main_content': body, 'notice': notice,
            'full_text': '\n\n'.join(filter(None, [body, notice])),
            '_content_version': 'skylife-campaign-v1',
            '_body_status': 'text' if body else ('image_only' if inner.select('img,svg') else 'unavailable'),
            '_notice_scope': 'skylife:campaign-accordion-v2',
            '_notice_complete': bool(targets and regions and notice and not missing),
            '_notice_regions': regions, '_notice_missing_regions': missing,
            '_notice_diagnostics': {'control_count': len(targets), 'outer_panel_count': len(outer)}}


def extract_skylife_content(driver, url):
    def ready(d):
        return read_skylife_dom(d.page_source).get('_detail_ready')
    try:
        WebDriverWait(driver, 15, poll_frequency=0.4).until(ready)
    except TimeoutException as error:
        raise RuntimeError('스카이라이프 이벤트 렌더링 대기 실패') from error
    started = time.monotonic()
    deadline = started + 45
    last, stable = None, 0
    while time.monotonic() < deadline:
        # Only campaign controls, never notification regions or site navigation.
        driver.execute_script('''
            const frame = document.querySelector('#iframe-promotion');
            let root = frame && frame.parentElement;
            while (root && root !== document.body &&
                   !root.querySelector('button[data-radix-collection-item][aria-controls]')) root = root.parentElement;
            if (!root || root === document.body) return;
            // Commit one React state update at a time. Batched clicks can
            // overwrite sibling expansion state before hydration has settled.
            const button = root.querySelector('button[data-radix-collection-item][aria-controls][data-state="closed"]');
            if (button) button.click();
        ''')
        time.sleep(0.25)
        result = read_skylife_dom(driver.page_source)
        fingerprint = repr(result.get('_notice_regions'))
        stable = stable + 1 if fingerprint == last else 0
        last = fingerprint
        if result.get('_notice_complete') and stable >= 2:
            return result
        if result.get('_detail_ready') and not result.get('_notice_regions') and stable >= 2 and time.monotonic() - started >= 3:
            # Some campaigns only contain an iframe image. Their loaded body
            # is usable, but absence of textual terms is still unconfirmed.
            return result
    result['_notice_complete'] = False
    return result
