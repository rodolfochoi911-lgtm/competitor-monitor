"""Read-only live extraction audit. Uses OS certificate validation; never sends alerts."""
import hashlib
import json
import sys
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from notice_regions import extract_regions, region_text
from content_extractor import extract_clean_text

CASES = [
 ('umobile-signature', 'https://www.uplusumobile.com/event-benefit/event/ongoing/12513'),
 ('umobile-code', 'https://www.uplusumobile.com/event-benefit/event/ongoing/12684'),
 ('ktm-senior', 'https://www.ktmmobile.com/event/eventDetail.do?ntcartSeq=939'),
 ('ktm-galaxy', 'https://www.ktmmobile.com/event/eventDetail.do?ntcartSeq=1856'),
 ('ktm-internet', 'https://www.ktmmobile.com/event/eventDetail.do?ntcartSeq=1490'),
 ('tdirect-roulette', 'https://shop.tworld.co.kr/exhibition/view?exhibitionId=P00000525'),
 ('tdirect-apple', 'https://shop.tworld.co.kr/exhibition/view?exhibitionId=P00000521'),
 ('sk7-cinema', 'https://www.sk7mobile.com/bnef/event/eventIngView.do?cntId=3cd529f1f7291aa968b97631c7d8fe52'),
 ('tdirect-definition', 'https://shop.tworld.co.kr/exhibition/view?exhibitionId=P00000517'),
 ('tdirect-accessibility', 'https://shop.tworld.co.kr/exhibition/view?exhibitionId=P00000506'),
 ('ktm-accessibility', 'https://www.ktmmobile.com/event/eventDetail.do?ntcartSeq=1868'),
 ('ktm-definition', 'https://www.ktmmobile.com/event/eventDetail.do?ntcartSeq=1458'),
 ('umobile-flat', 'https://www.uplusumobile.com/event-benefit/event/ongoing/12641'),
 ('umobile-nested', 'https://www.uplusumobile.com/event-benefit/event/ongoing/567'),
]


def main():
    dest = Path(sys.argv[1])
    dest.mkdir(parents=True, exist_ok=True)
    results = []
    for label, url in CASES:
        observations = []
        for attempt in range(2):
            try:
                request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(request, timeout=25) as response:
                    data = response.read()
                    charset = response.headers.get_content_charset() or 'utf-8'
                html = data.decode(charset, errors='replace')
                (dest / f'{label}-{attempt}.html').write_text(html, encoding='utf-8')
                extracted = extract_regions(html, url, region_text)
                observations.append({**extracted, 'hash': hashlib.sha256(extracted['notice'].encode()).hexdigest()})
            except Exception as error:
                observations.append({'error': str(error), '_notice_complete': False})
        same = (len(observations) == 2 and observations[0].get('hash') is not None
                and observations[0].get('hash') == observations[1].get('hash'))
        row = {'label': label, 'url': url, 'same': same, 'observations': observations}
        results.append(row)
        print(label, 'complete=', [o['_notice_complete'] for o in observations], 'same=', same,
              'panels=', [len(o.get('_notice_regions', [])) for o in observations], flush=True)
    (dest / 'results.json').write_text(json.dumps({'at':datetime.now(timezone.utc).isoformat(), 'results':results}, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
