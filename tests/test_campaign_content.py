import unittest
from bs4 import BeautifulSoup
from campaign_content import extract_campaign_body
from notice_regions import extract_regions, region_text
from monitor_core import collection_warnings, detect_changes, calculate_notice_diff

KTM = 'https://www.ktmmobile.com/event/eventDetail.do?ntcartSeq=937'
HELLO = 'https://direct.lghellovision.net/event/viewEventDetail.do?idxOfEvent=586'


class CampaignContentTests(unittest.TestCase):
    def ktm(self, hidden='', amount='6,400', summary='1만원 X 6개월'):
        return f'''<div class="c-board__content"><article class="c-editor">
        <p>모두다 맘껏 안심 2.5GB+ 월 {amount}원</p>
        <div class="rate-banner event-summary" {hidden}><div class="c-accordion--summary">
        <p>모바일 혜택</p><p>이벤트코드 M 입력 시 {summary}</p></div></div>
        <div class="rate-banner event-summary" style="display:none"><p>1.5만원 X 8개월</p></div>
        <div id="PRMT0007"><div class="attention-box"><h2>유의사항</h2>
        <button aria-controls="terms">공통</button><div id="terms">개통 후 8개월간 지급</div></div></div>
        <div id="PRMT0023">다른 이벤트도 살펴보세요 랜덤 배너</div>
        </article></div><footer>푸터</footer>'''

    def test_rate_variants_survive_visibility_change_without_dedup(self):
        open_body = extract_campaign_body(self.ktm(), KTM)
        closed_body = extract_campaign_body(self.ktm('style="display:none"'), KTM)
        self.assertEqual(open_body['main_content'], closed_body['main_content'])
        self.assertIn('1만원 X 6개월', closed_body['main_content'])
        self.assertIn('1.5만원 X 8개월', closed_body['main_content'])
        self.assertNotIn('랜덤 배너', open_body['main_content'])
        self.assertEqual({}, detect_changes(open_body, closed_body))

    def test_actual_rate_or_summary_change_is_reported(self):
        before = extract_campaign_body(self.ktm(), KTM)
        for html in (self.ktm(amount='7,400'), self.ktm(summary='2만원 X 8개월')):
            self.assertIn('main_content', detect_changes(before, extract_campaign_body(html, KTM)))

    def test_official_prmt_terms_are_not_removed(self):
        before = extract_regions(self.ktm(), KTM, region_text)
        self.assertTrue(before['_notice_complete'])
        self.assertIn('8개월간 지급', before['notice'])

    def hello(self, next_title='다음글이 없습니다.', date='10월 23일'):
        return f'''<div class="boardView"><div class="substance wide">
        <p>가입 시 월 5,000원 할인</p><div class="noticeWrap noticeWrapNew"><h5>유의사항</h5>
        <dl class="viewNotice" id="newViewNotice"><dt>지급 일정</dt><dd>1회차 : {date}</dd></dl>
        </div></div></div><div class="pageGo"><ul id="prevNext"><li>{next_title}</li></ul></div>
        <div id="footer">푸터 상품안내</div><div role="dialog">월 납부금액 추천 요금제</div>'''

    def test_hello_navigation_footer_and_popup_never_become_campaign(self):
        before = extract_campaign_body(self.hello(), HELLO)
        after = extract_campaign_body(self.hello('월 4만원 타임딜 이벤트'), HELLO)
        self.assertEqual('가입 시 월 5,000원 할인', before['main_content'])
        self.assertEqual({}, detect_changes(before, after))
        notice = extract_regions(self.hello(), HELLO, region_text)
        self.assertTrue(notice['_notice_complete'])
        self.assertEqual(1, len(notice['_notice_regions']))
        self.assertNotIn('푸터', notice['notice'])

    def test_hello_real_payment_date_change_still_detected(self):
        def snapshot(date):
            e = extract_regions(self.hello(date=date), HELLO, region_text)
            return {'헬로모바일': {HELLO: e}}
        result = calculate_notice_diff(snapshot('11월 23일'), snapshot('10월 23일'))
        self.assertTrue(result['헬로모바일']['added'])
        self.assertTrue(result['헬로모바일']['removed'])

    def test_missing_scoped_root_fails_without_falling_back_to_footer(self):
        with self.assertRaises(RuntimeError):
            extract_campaign_body('<footer>할인 5만원 안내</footer>', HELLO)

    def test_notice_scope_correction_is_reviewable_once(self):
        from monitor_core import preserve_unavailable_fields
        def event(scope, value):
            return {'notice': value, '_notice_observation': {'version': 2, 'status': 'verified',
                    'attempts': [{'scope': scope}]}}
        old = {'헬로': {HELLO: event('#contents', '잘못 포함된 주변 안내')}}
        new = {'헬로': {HELLO: event('hello:official', '1회차 11월 23일')}}
        preserve_unavailable_fields(new, old)
        self.assertTrue(calculate_notice_diff(new, old)['헬로']['review_required'])
        again = {'헬로': {HELLO: event('hello:official', '1회차 11월 23일')}}
        preserve_unavailable_fields(again, new)
        self.assertNotIn('_notice_transition', again['헬로'][HELLO])

    def test_collection_messages_group_by_company_without_provenance_jargon(self):
        events = {str(i): {'_retained_fields': {'main_content': 'old.json', 'notice': 'old.json'},
                          '_notice_pending': {'changed': True, 'observed': ''}} for i in range(20)}
        warnings = collection_warnings({'KTM': events})
        self.assertEqual(1, len(warnings))
        self.assertIn('본문 20건', warnings[0])
        self.assertIn('유의사항 20건', warnings[0])
        self.assertNotIn('이전 수집값 보존', warnings[0])
        self.assertNotIn('old.json', warnings[0])
        self.assertNotIn('변경 후보', warnings[0])


if __name__ == '__main__':
    unittest.main()
