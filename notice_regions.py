"""Explicit event notice regions. Hidden panels are content, dialogs are not."""
from urllib.parse import urlsplit
import re
from bs4 import BeautifulSoup, NavigableString, Comment


def region_text(node):
    """Keep inline phrases together and repeated contractual values intact."""
    blocks = {'p', 'li', 'div', 'section', 'dd', 'dt', 'h1', 'h2', 'h3', 'h4', 'tr'}
    def render(item):
        if isinstance(item, Comment):
            return ''
        if isinstance(item, NavigableString):
            return re.sub(r'\s+', ' ', str(item))
        if item.name in {'script', 'style', 'noscript', 'button'}:
            return ''
        if item.name == 'br':
            return '\n'
        value = ''.join(render(child) for child in item.children)
        return '\n' + value + '\n' if item.name in blocks else value
    return '\n'.join(line.strip() for line in render(node).splitlines() if line.strip())


def region_spec(url):
    host = (urlsplit(url).hostname or '').lower()
    return {
        'www.sk7mobile.com': ('sk7', '.noticeContainer'),
        'www.uplusumobile.com': ('umobile', 'section.template-info.unit-origin'),
        'www.ktmmobile.com': ('ktm', '.attention-box'),
        'shop.tworld.co.kr': ('tdirect', '.arcBoxWrap'),
        'direct.lghellovision.net': ('hello', '.boardView .substance .noticeWrapNew, .boardView .substance .viewNotice'),
    }.get(host)


def supplemental_panels(root):
    """Explicit definition/heading notes and image accessibility copy in a campaign."""
    selected = []
    pattern = re.compile(r'유의\s*사항|주의\s*사항|꼭\s*확인')
    for heading in root.find_all(['dt', 'h2', 'h3', 'h4', 'h5', 'p', 'strong', 'b']):
        label = heading.get_text(' ', strip=True)
        if len(label) > 100 or not pattern.search(label):
            continue
        accessible = heading.find_parent(class_=lambda value: value in ('c-hidden', 'img-blind'))
        if accessible is not None:
            selected.append((label, accessible))
            continue
        anchor = heading
        if heading.name in ('strong', 'b') and heading.parent.name in ('p', 'dt'):
            anchor = heading.parent
        for sibling in anchor.next_siblings:
            if not getattr(sibling, 'name', None):
                continue
            if sibling.name in ('dt', 'h2', 'h3', 'h4', 'h5'):
                break
            if sibling.name in ('dd', 'ul', 'ol', 'p', 'div'):
                selected.append((label, sibling))
    # Do not collect rotating related-event banners as campaign terms.
    for node in root.select('.c-hidden, .img-blind'):
        text = node.get_text(' ', strip=True)
        if (not node.find_parent(id=re.compile(r'^PRMT'))
                and re.search(r'유의|주의|※|유효기간|지급|제외|해지', text)):
            selected.append(('이벤트 이미지 대체설명', node))
    unique = []
    for title, node in selected:
        if any(node is other or other in node.parents for _, other in unique):
            continue
        unique = [(t, other) for t, other in unique if node not in other.parents]
        unique.append((title, node))
    return [(f'plain:{i}', title, node) for i, (title, node) in enumerate(unique)]


def extract_regions(html, url, clean_text):
    spec = region_spec(url)
    if spec is None:
        return None
    name, selector = spec
    soup = BeautifulSoup(html, 'html.parser')
    # A hidden modal has real text too. It must never establish a notice baseline.
    for node in soup.select('#trgtSupmOfferPop, .modal, .layer-popup, [role="dialog"], '
                            '[aria-modal="true"], template, script, style, noscript'):
        node.decompose()
    roots = soup.select(selector)
    # A viewNotice can be nested within noticeWrapNew. Read each notice once.
    if name == 'hello':
        roots = [r for r in roots if not any(parent in roots for parent in r.parents)]
    if name == 'ktm':
        roots = [r for r in roots if any('유의사항' in h.get_text() for h in r.select('h2,h3,h4'))]
    if name == 'umobile':
        # Recommendations reuse the same section class as actual terms.
        roots = [r for r in roots if any('유의사항' in h.get_text() for h in r.select('.fix-info-tit h2,h3'))
                 or (r.find(['h2', 'h3']) is None and r.select('.acc-trigger') and not r.select('.event-567'))]
    panels, missing = [], []
    for root_index, root in enumerate(roots):
        if root.get('aria-busy') == 'true' or root.select('[aria-busy="true"]'):
            missing.append(f'{root_index}:loading')
        if name == 'hello':
            if len(clean_text(root)) < 10:
                missing.append(f'{root_index}:empty')
            panels.append((str(root_index), '유의사항', root))
        elif name == 'umobile':
            # This official section also has flat and nested notice templates.
            # Read the section, not an assumed one-button/one-panel count.
            for index, panel in enumerate(root.select('.acc-conts')):
                if not clean_text(panel):
                    missing.append(f'{root_index}:{index}')
            text = clean_text(root)
            if len(text) < 10:
                missing.append(f'{root_index}:empty')
            panels.append((str(root_index), '유의사항', root))
        elif name == 'ktm':
            controls = root.select('button[aria-controls]')
            if not controls:
                missing.append(f'{root_index}:controls')
            for button in controls:
                for target in button.get('aria-controls', '').split():
                    panel = root.find(id=target)
                    if panel is None or not clean_text(panel):
                        missing.append(target)
                    else:
                        title = button.get_text(' ', strip=True).replace('상세열기', '').replace('상세 열기', '').strip()
                        panels.append((target, title, panel))
        else:
            panel_selector = {'sk7': '.acc-body', 'umobile': '.acc-conts', 'tdirect': '.arcBox .a'}[name]
            found = root.select(panel_selector)
            if not found:
                missing.append(f'{root_index}:panels')
            if name == 'sk7' and len(root.select('.acc-head')) != len(found):
                missing.append(f'{root_index}:control-panel-mismatch')
            if name == 'tdirect' and len(root.select('.arcBox .q')) != len(found):
                missing.append(f'{root_index}:control-panel-mismatch')
            for index, panel in enumerate(found):
                key = f'{root_index}:{index}'
                if not clean_text(panel):
                    missing.append(key)
                if name == 'sk7':
                    heading = panel.find_previous_sibling('dt')
                elif name == 'umobile':
                    heading = panel.find_previous_sibling('button')
                else:
                    heading = panel.find_previous_sibling('a')
                title = heading.get_text(' ', strip=True) if heading is not None else ''
                panels.append((key, title, panel))
    if name in ('ktm', 'tdirect') and not roots:
        campaign = soup.select_one('.c-board__content' if name == 'ktm' else '.event-detail-content')
        if campaign is not None:
            for node in campaign.select('[id^="PRMT"]'):
                node.decompose()
            extras = supplemental_panels(campaign)
            # Existing accordion bodies already cover these notices.
            panels.extend((key, title, node) for key, title, node in extras
                          if not any(node is panel or panel in node.parents or node in panel.parents for _, _, panel in panels))
            if not roots and panels:
                roots = [campaign]
    values = []
    for key, title, panel in panels:
        text = clean_text(panel)
        if text:
            values.append({'id': key, 'title': title, 'text': text})
    # Do not deduplicate across panels: the same sentence may belong to two terms.
    text = '\n'.join(('[' + p['title'] + ']\n' if p['title'] else '') + p['text'] for p in values)
    return {'notice': text, '_notice_scope': f'{name}:{selector}',
            '_notice_complete': bool(roots and panels and not missing and len(values) == len(panels)),
            '_notice_regions': values, '_notice_missing_regions': missing or ([] if roots else [selector]),
            '_notice_diagnostics': {'root_count': len(roots), 'panel_count': len(panels),
                                    'panel_states': [{'id': key, 'aria_hidden': node.get('aria-hidden'),
                                                      'style': node.get('style', '')} for key, _, node in panels]}}
