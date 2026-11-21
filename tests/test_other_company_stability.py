import copy
import unittest
from unittest.mock import Mock
from campaign_content import extract_campaign_body
from skylife_content import read_skylife_dom
from notice_observation import collect_verified_notice
from monitor_core import detect_changes, preserve_unavailable_fields, calculate_notice_diff, collection_warnings


class OtherCompanyStabilityTests(unittest.TestCase):
    def test_tdirect_body_is_independent_of_notice_state_and_modal(self):
        def html(hidden, price='30만원'):
            return f'''<div class="event-detail-content"><div class="event-detail">
            <p>룰렛 최대 {price} 할인</p><span class="img-blind">1TB 대상 최대 30만원 할인</span>
            <div class="arcBoxWrap" {hidden}><div class="arcBox"><div class="a">약정 유지 시 지급</div></div></div>
            <div class="modal" id="trgtSupmOfferPop">나만의 추가할인 90만원</div></div></div>'''
        url = 'https://shop.tworld.co.kr/exhibition/view?exhibitionId=P00000525'
        before = extract_campaign_body(html(''), url)
        after = extract_campaign_body(html('style="display:none" aria-hidden="true"'), url)
        self.assertEqual({}, detect_changes(before, after))
        self.assertNotIn('약정 유지', before['main_content'])
        self.assertNotIn('90만원', before['main_content'])
        self.assertIn('1TB 대상 최대 30만원', before['main_content'])
        self.assertIn('main_content', detect_changes(before, extract_campaign_body(html('', '40만원'), url)))

    def test_umobile_keeps_accessibility_prices_and_repeated_amounts(self):
        def html(price):
            return f'''<main><div class="sub-conts template evt-default-template">
            <section class="template-tit">제목 날짜 공유</section>
            <section class="template-view"><span class="i_blind">갤럭시 정상가 880,000원, 회원가 {price}원</span>
            <p>혜택 A 5만원</p><p>혜택 B 5만원</p></section>
            <section class="template-info unit-origin"><h2>다른 이벤트도 살펴보세요</h2><p>랜덤 광고</p></section>
            <section class="template-info unit-origin"><h2>유의사항</h2><p>해지 시 지급 제외</p></section>
            </div><div role="dialog">로그인 상품 추천</div></main>'''
        url = 'https://www.uplusumobile.com/event-benefit/event/ongoing/12641'
        before = extract_campaign_body(html('792,000'), url)
        self.assertIn('880,000원', before['main_content'])
        self.assertIn('792,000원', before['main_content'])
        self.assertEqual(2, before['main_content'].count('5만원'))
        self.assertNotIn('랜덤 광고', before['main_content'])
        self.assertNotIn('해지 시', before['main_content'])
        self.assertIn('main_content', detect_changes(before, extract_campaign_body(html('700,000'), url)))

    def test_umobile_actual_period_change_survives_header_removal(self):
        def event(day):
            return extract_campaign_body(f'<main><div class="sub-conts template"><section class="template-tit">2026-10-01 ~ 2026-10-{day}</section><section class="template-view"><img src="a.png"></section></div></main>',
                                         'https://www.uplusumobile.com/event-benefit/event/ongoing/12684')
        self.assertIn('event_period', detect_changes(event('29'), event('31')))

    def sky(self, amount='5만원', child=True):
        return f'''<div class="campaign"><section><iframe id="iframe-promotion" srcdoc="&lt;img src='/body.png'&gt;"></iframe></section>
        <button data-radix-collection-item aria-controls="outer" data-state="open">안내 및 유의사항</button>
        <div id="outer" role="region" data-state="open"><p>일반 안내 {amount}</p>
        <button data-radix-collection-item aria-controls="inner" data-state="open">사은품 안내</button>
        <div id="inner" role="region" data-state="open">{'추가 사은품 ' + amount if child else ''}</div></div></div>
        <div role="region">알림센터 텍스트</div>'''

    def test_skylife_reads_final_nested_tree_once(self):
        result = read_skylife_dom(self.sky())
        self.assertTrue(result['_notice_complete'])
        self.assertEqual(1, len(result['_notice_regions']))
        self.assertEqual(2, result['notice'].count('5만원'))
        self.assertEqual('image_only', result['_body_status'])
        self.assertNotIn('알림센터', result['notice'])
        self.assertNotEqual(result['notice'], read_skylife_dom(self.sky('7만원'))['notice'])

    def test_skylife_missing_nested_text_never_confirms(self):
        self.assertFalse(read_skylife_dom(self.sky(child=False))['_notice_complete'])
        self.assertFalse(read_skylife_dom('<div>Loading...</div>')['_detail_ready'])
        image_only = read_skylife_dom('<iframe id="iframe-promotion" srcdoc="&lt;img src=\'a.png\'&gt;"></iframe>')
        self.assertTrue(image_only['_detail_ready'])
        self.assertFalse(image_only['_notice_complete'])

    def test_skylife_error_screen_is_not_campaign_content(self):
        with self.assertRaisesRegex(RuntimeError, '오류 화면'):
            read_skylife_dom('<h2>Application error: a client-side exception has occurred</h2>')
        old = {'title': 'Application error: a client-side exception has occurred'}
        self.assertEqual({}, detect_changes(old, {'title': '원래 이벤트 제목'}))

    def test_all_exceptions_cannot_be_returned_as_success(self):
        with self.assertRaises(RuntimeError):
            collect_verified_notice(Mock(), 'url', Mock(side_effect=RuntimeError('오류 화면')))

    def test_last_failed_attempt_cannot_reuse_earlier_success(self):
        extract = Mock(side_effect=[{'notice': '5만원', '_notice_complete': True}, RuntimeError('실패'), RuntimeError('실패')])
        with self.assertRaises(RuntimeError):
            collect_verified_notice(Mock(), 'url', extract)

    def test_one_failed_detail_keeps_baseline_and_other_events_can_change(self):
        previous = {'스카이라이프': {'bad': {'title': '실제 제목', 'main_content': '실제 본문', 'notice': '5만원'},
                                  'good': {'title': '정상 이벤트', 'notice': '5만원'}}}
        current = {'스카이라이프': {'bad': {'title': 'Application error:', '_detail_collection_error': '렌더링 실패'},
                                 'good': {'title': '정상 이벤트', 'notice': '7만원',
                                          '_notice_observation': {'status': 'verified', 'version': 2}}}}
        preserve_unavailable_fields(current, previous)
        self.assertEqual('실제 제목', current['스카이라이프']['bad']['title'])
        self.assertEqual({}, detect_changes(previous['스카이라이프']['bad'], current['스카이라이프']['bad']))
        delta = calculate_notice_diff(current, previous)['스카이라이프']
        self.assertIn('7만원', delta['added'])
        self.assertTrue(all(s['url'] == 'good' for s in delta['added_sources'].values()))
        self.assertIn('상세 수집 실패 1건', collection_warnings(current)[0])
        recovered = copy.deepcopy(previous)
        preserve_unavailable_fields(recovered, current)
        self.assertNotIn('_detail_collection_error', recovered['스카이라이프']['bad'])


if __name__ == '__main__':
    unittest.main()
