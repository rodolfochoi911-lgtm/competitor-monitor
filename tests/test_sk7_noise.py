import unittest
from unittest.mock import patch, Mock
from bs4 import BeautifulSoup
from promotion_noise import stable_text
from monitor_core import detect_changes, calculate_notice_diff, collection_warnings, preserve_unavailable_fields
from detail_image_monitor import extract_detail_image_urls, hash_detail_images
import parser

PAGE = 'https://www.sk7mobile.com/bnef/event/eventIngView.do?cntId=test'


class SK7NoiseTests(unittest.TestCase):
    def test_counter_variants_and_inline_dates(self):
        for counter in ['조회수: 1,234회', '조회 수 56', '조회 77', 'D-10', 'd − 9', 'D-DAY', 'D + 1', '3일 남음']:
            with self.subTest(counter=counter):
                text = '신청기간 2026-10-01 ~ 2026-10-31 ' + counter
                self.assertEqual('신청기간 2026-10-01 ~ 2026-10-31', stable_text(text))

    def test_split_counter_markup(self):
        self.assertEqual('혜택', stable_text('<p>조회수 <span>1,234</span></p><p>혜택</p>'))

    def test_actual_durations_and_standalone_dates_survive(self):
        value = '2026-10-31\n2026년 11월 1일\n개통 후 30일 이내 지급\n12개월 유지'
        self.assertEqual(value, stable_text(value))
        from content_extractor import extract_clean_text
        self.assertIn('2026-10-31', extract_clean_text(BeautifulSoup('<p>2026-10-31</p>', 'html.parser')))

    def test_counters_do_not_modify_body_title_or_notice(self):
        old = {'title': '행사 D-3', 'main_content': '혜택 조회수: 1,000', 'notice': '기간 10/31까지 D-3'}
        new = {'title': '행사 D-2', 'main_content': '혜택 조회수: 1,500', 'notice': '기간 10/31까지 D-2'}
        self.assertEqual({}, detect_changes(old, new))
        self.assertEqual({}, calculate_notice_diff({'A': {'u': new}}, {'A': {'u': old}}))

    def test_body_and_notice_dates_are_detected(self):
        old = {'main_content': '2026-10-31', 'notice': '2026년 10월 31일'}
        new = {'main_content': '2026-11-30', 'notice': '2026년 11월 30일'}
        self.assertIn('main_content', detect_changes(old, new))
        self.assertTrue(calculate_notice_diff({'A': {'u': new}}, {'A': {'u': old}}))

    def test_explicit_period_change_and_first_baseline(self):
        self.assertEqual({}, detect_changes({}, {'event_period': '2026-10-01 ~ 2026-10-31'}))
        self.assertIn('event_period', detect_changes({'event_period': '2026-10-31'}, {'event_period': '2026-11-30'}))
        self.assertIn('event_period', detect_changes(
            {'main_content': '기간\n2026-10-01\n~\n2026-10-31'},
            {'event_period': '2026-10-01 ~ 2026-11-30'}))

    def test_title_date_range_is_material(self):
        from main import clean_title
        self.assertEqual('행사 2026-10-01~2026-10-31', clean_title('행사 2026-10-01~2026-10-31 D-3'))
        self.assertFalse(parser.is_timestamp_line('2026-10-31'))

    def test_schema_migration_is_not_an_image_or_body_edit(self):
        old = {'detail_image_hashes': ['banner', 'event'], 'main_content': 'page chrome'}
        new = {'detail_image_hashes': ['event'], '_image_hash_version': 2,
               'main_content': 'event text', '_content_version': 2}
        self.assertEqual({}, detect_changes(old, new))
        self.assertIn('detail_image_hashes', detect_changes(new, dict(new, detail_image_hashes=['replacement'])))

    def test_scoped_images_exclude_shared_banner_and_recommendations(self):
        html = '''<div id="ct"><img src="/other.png"><div class="bbs-view"><div class="view-body">
        <div class="eventCont"><div class="evt-strip"><img src="/banner.png"></div>
        <img src="/event.png"><div class="dday"><img src="/counter.png"></div></div>
        <img src="/telink/upload/event-recommend/other.png"></div></div></div>'''
        self.assertEqual(['https://www.sk7mobile.com/event.png'], extract_detail_image_urls(html, PAGE))

    def test_image_cap_does_not_silently_compare_partial_set(self):
        html = '<main>' + ''.join(f'<img src="/{i}.png">' for i in range(25)) + '</main>'
        hashes, warning = hash_detail_images(html, PAGE, session=Mock())
        self.assertIsNone(hashes)
        self.assertIn('상한 초과', warning)

    def test_image_failure_is_diagnostic_and_baseline_is_retained(self):
        old = {'A': {'u': {'detail_image_hashes': ['good'], '_image_hash_version': 2}}}
        new = {'A': {'u': {'_image_collection_error': 'download failed', '_image_hash_version': 2}}}
        self.assertEqual([], preserve_unavailable_fields(new, old))
        self.assertEqual(['good'], new['A']['u']['detail_image_hashes'])
        self.assertIn('_image_collection_error', new['A']['u'])
        self.assertEqual([], collection_warnings(new))

    def test_slack_disables_link_and_media_previews(self):
        with patch.object(parser, 'slack_webhook_url', 'https://example.invalid'), patch.object(parser.requests, 'post') as post:
            parser.send_slack_report(1, ['• SK7: 이미지 변경'], {})
        payload = post.call_args.kwargs['json']
        self.assertFalse(payload['unfurl_links'])
        self.assertFalse(payload['unfurl_media'])

    def test_scoped_text_excludes_comments_and_chrome(self):
        from content_extractor import extract_page_content
        driver = Mock()
        driver.page_source = '<div id="ct">챗봇<div class="bbs-view"><div class="view-head">기간 2026-10-31</div><div class="view-body"><div class="eventCont">' + '<p>개통 혜택을 받을 수 있습니다.</p>' * 6 + '<!-- comment date 2026.01.01 --><p class="dday">D-3</p></div></div></div></div>'
        with patch('content_extractor.wait_and_expand', return_value=0):
            result = extract_page_content(driver, PAGE)
        self.assertNotIn('챗봇', result['full_text'])
        self.assertNotIn('comment', result['full_text'])
        self.assertNotIn('D-3', result['full_text'])
