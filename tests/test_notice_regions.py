import unittest
from bs4 import BeautifulSoup
from notice_regions import extract_regions, region_text


def clean(el):
    return el.get_text('\n', strip=True)


class RegionTests(unittest.TestCase):
    def test_inline_values_and_repeated_amounts_are_preserved(self):
        soup = BeautifulSoup('<div><p>가입 <span>5</span><b>만원</b> 지급</p><p>해지 시 <span>5</span><b>만원</b> 반환</p></div>', 'html.parser')
        self.assertEqual('가입 5만원 지급\n해지 시 5만원 반환', region_text(soup.div))

    def test_recommendation_section_does_not_make_valid_notice_incomplete(self):
        html = '''<section class="template-info unit-origin"><button class="acc-trigger">유의사항</button>
        <div class="acc-conts">상품권 5만원</div></section>
        <section class="template-info unit-origin"><h2>다른 이벤트도 살펴보세요.</h2></section>'''
        self.assertTrue(self.read('www.uplusumobile.com', html)['_notice_complete'])
    def read(self, host, html):
        return extract_regions(html, 'https://' + host + '/event', clean)

    def test_tdirect_popup_never_preempts_event_terms(self):
        html = '''<div id="trgtSupmOfferPop" class="modal"><div class="noticeWrap">추가지원금 5만원</div></div>
        <div class="arcBoxWrap"><ul class="arcBox"><li><a class="q">유의 사항</a>
        <div class="a" style="display:none"><ul><li>추첨 당첨 시 30만원</li><li>중복 참여 불가</li></ul></div></li></ul></div>'''
        r = self.read('shop.tworld.co.kr', html)
        self.assertTrue(r['_notice_complete'])
        self.assertIn('중복 참여 불가', r['notice'])
        self.assertNotIn('추가지원금', r['notice'])

    def test_tdirect_missing_actual_region_is_incomplete(self):
        r = self.read('shop.tworld.co.kr', '<div class="noticeWrap">추가지원금 5만원</div>')
        self.assertFalse(r['_notice_complete'])
        self.assertEqual('', r['notice'])

    def test_umobile_hidden_and_open_return_same_complete_text(self):
        html = '''<section class="template-info unit-origin"><button class="acc-trigger">유의사항</button>
        <div class="acc-conts" aria-hidden="STATE" style="display:DISPLAY"><ul><li>다이소 5천원 매월 지급</li>
        <li>명의 변경 시 제외</li></ul></div></section><div class="layer-popup terms">다른 약관</div>'''
        a = self.read('www.uplusumobile.com', html.replace('STATE', 'true').replace('DISPLAY', 'none'))
        b = self.read('www.uplusumobile.com', html.replace('STATE', 'false').replace('DISPLAY', 'block'))
        self.assertTrue(a['_notice_complete'])
        self.assertEqual(a['notice'], b['notice'])
        self.assertNotIn('다른 약관', a['notice'])

    def test_umobile_one_unloaded_panel_is_not_complete(self):
        html = '''<section class="template-info unit-origin"><button class="acc-trigger">a</button>
        <div class="acc-conts">상품권 7만원</div><button class="acc-trigger">b</button>
        <div class="acc-conts"></div></section>'''
        r = self.read('www.uplusumobile.com', html)
        self.assertFalse(r['_notice_complete'])
        self.assertIn('7만원', r['notice'])
        self.assertTrue(r['_notice_missing_regions'])

    def test_ktm_aria_true_css_hidden_does_not_lose_panels(self):
        html = '''<div class="attention-box"><h3>유의사항</h3><button aria-expanded="true" aria-controls="p1">공통 상세열기</button>
        <div id="p1" class="c-accordion__panel" style="display:none">상품권 5만원</div>
        <button aria-expanded="true" aria-controls="p2">이벤트</button><div id="p2">해지 시 지급 제외</div></div>
        <div class="info-area">추천 요금제 3만원</div>'''
        r = self.read('www.ktmmobile.com', html)
        self.assertTrue(r['_notice_complete'])
        self.assertIn('해지 시 지급 제외', r['notice'])
        self.assertNotIn('추천 요금제', r['notice'])
        self.assertNotIn('상세열기', r['notice'])

    def test_ktm_missing_control_target_is_incomplete(self):
        r = self.read('www.ktmmobile.com', '<div class="attention-box"><h3>유의사항</h3><button aria-controls="missing">공통</button></div>')
        self.assertFalse(r['_notice_complete'])
        self.assertIn('missing', r['_notice_missing_regions'])

    def test_busy_region_is_not_confirmed_even_with_visible_old_text(self):
        html = '''<div class="attention-box" aria-busy="true"><h3>유의사항</h3>
        <button aria-controls="p">공통</button><div id="p">상품권 5만원</div></div>'''
        self.assertFalse(self.read('www.ktmmobile.com', html)['_notice_complete'])

    def test_umobile_flat_official_section_has_no_accordion_requirement(self):
        html = '''<section class="template-info unit-origin"><div class="fix-info-tit"><h2>유의사항</h2></div>
        <ul class="notification"><li>가입 후 명의변경 시 할인 종료</li><li>월 중 해지 시 정상요금 일할 계산</li></ul></section>'''
        r = self.read('www.uplusumobile.com', html)
        self.assertTrue(r['_notice_complete'])
        self.assertIn('명의변경', r['notice'])

    def test_umobile_image_qna_outside_official_terms_is_excluded(self):
        html = '''<section class="template-info unit-origin"><div class="event-567"><button class="acc-trigger">QNA</button><div class="acc-conts"><img src="qna.png"></div></div></section>
        <section class="template-info unit-origin"><div class="fix-info-tit"><h2>유의사항</h2></div><button class="acc-trigger">VIP</button><div class="acc-conts">매월 1회 지급</div></section>'''
        r = self.read('www.uplusumobile.com', html)
        self.assertTrue(r['_notice_complete'])
        self.assertNotIn('QNA', r['notice'])

    def test_tdirect_definition_terms_are_scoped_to_campaign(self):
        html = '''<div class="noticeWrap">개인화 추가지원금 5만원</div>
        <div class="event-detail-content"><h1>OTT 행사</h1><dl><dt>경품 혜택</dt><dd>항공권</dd>
        <dt>유의 사항</dt><dd>당첨자 발표일까지 이용 고객 한정</dd></dl></div>'''
        r = self.read('shop.tworld.co.kr', html)
        self.assertTrue(r['_notice_complete'])
        self.assertIn('당첨자 발표일까지', r['notice'])
        self.assertNotIn('추가지원금', r['notice'])
        self.assertNotIn('항공권', r['notice'])

    def test_ktm_accessibility_copy_is_retained_but_rotating_banner_is_not(self):
        html = '''<div class="c-board__content"><div id="PRMT0019"><div class="c-hidden">다른 이벤트 50만원 지급</div></div>
        <div class="c-hidden"><strong>주의사항</strong><p>ID당 매월 1회 등록 가능합니다.</p></div></div>'''
        r = self.read('www.ktmmobile.com', html)
        self.assertTrue(r['_notice_complete'])
        self.assertIn('매월 1회', r['notice'])
        self.assertNotIn('50만원', r['notice'])

    def test_ktm_shared_module_containing_official_terms_is_not_removed(self):
        html = '''<div class="c-board__content"><div id="PRMT0022"><div class="attention-box"><h3>유의사항</h3>
        <button aria-controls="p">공통 상세열기</button><div id="p">명의 변경 시 혜택 종료</div></div></div></div>'''
        r = self.read('www.ktmmobile.com', html)
        self.assertTrue(r['_notice_complete'])
        self.assertIn('명의 변경 시 혜택 종료', r['notice'])

    def test_sk7_panel_states_do_not_change_extracted_text(self):
        html = '''<div class="noticeContainer"><div class="acc-head">10월 유의사항</div>
        <dd class="acc-body" aria-hidden="true">일요일 개통 불가</dd></div>'''
        a = self.read('www.sk7mobile.com', html)
        b = self.read('www.sk7mobile.com', html.replace('true', 'false'))
        self.assertEqual(a['notice'], b['notice'])
        self.assertTrue(a['_notice_complete'])
