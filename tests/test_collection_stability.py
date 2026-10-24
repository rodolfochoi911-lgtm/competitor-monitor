import copy
import unittest
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup
import parser
from content_extractor import extract_page_content, _click_accordions, _safe_click
from monitor_core import preserve_unavailable_fields, calculate_notice_diff, collection_warnings, detect_changes

PAGE = 'https://www.sk7mobile.com/bnef/event/eventIngView.do?cntId=test'


class CollectionStabilityTests(unittest.TestCase):
    def snapshot(self, notice):
        return {'SK7': {PAGE: {'title': '혜택', 'notice': notice}}}

    def test_partial_notice_recovery_never_alerts(self):
        full = '상품권 5만원 지급\n개통 불가 일정: 10월 4일, 10월 11일'
        previous = self.snapshot(full)
        for observed in ['상품권 5만원 지급', full, '상품권 5만원 지급', full]:
            current = self.snapshot(observed)
            self.assertEqual([], preserve_unavailable_fields(current, previous, 'previous.json'))
            self.assertEqual({}, calculate_notice_diff(current, previous))
            self.assertEqual(full, current['SK7'][PAGE]['notice'])
            previous = current

    def test_confirmed_amount_change_alerts_once(self):
        previous = self.snapshot('상품권 5만원 지급')
        first = self.snapshot('상품권 7만원 지급')
        preserve_unavailable_fields(first, previous)
        self.assertEqual({}, calculate_notice_diff(first, previous))
        second = self.snapshot('상품권 7만원 지급')
        preserve_unavailable_fields(second, first)
        delta = calculate_notice_diff(second, first)['SK7']
        self.assertEqual(['상품권 7만원 지급'], delta['added'])
        self.assertEqual(['상품권 5만원 지급'], delta['removed'])
        third = self.snapshot('상품권 7만원 지급')
        preserve_unavailable_fields(third, second)
        self.assertEqual({}, calculate_notice_diff(third, second))
        self.assertNotIn('_notice_candidate', second['SK7'][PAGE])

    def test_confirmed_paragraph_removal_is_detected(self):
        previous = self.snapshot('상품권 5만원 지급\n개통 불가 일정: 10월 4일')
        first = self.snapshot('상품권 5만원 지급')
        preserve_unavailable_fields(first, previous)
        second = self.snapshot('상품권 5만원 지급')
        preserve_unavailable_fields(second, first)
        self.assertEqual(['개통 불가 일정: 10월 4일'], calculate_notice_diff(second, first)['SK7']['removed'])

    def test_verified_image_body_drops_stale_text_without_warning(self):
        previous = {'SK7': {PAGE: {'main_content': '오래된 본문', '_content_version': 2,
                    '_retained_fields': {'main_content': 'old.json'}}}}
        current = {'SK7': {PAGE: {'main_content': '', '_body_status': 'image_only',
                   '_content_version': 4, 'detail_image_hashes': ['verified-image']}}}
        self.assertEqual([], preserve_unavailable_fields(current, previous))
        self.assertEqual('', current['SK7'][PAGE]['main_content'])
        self.assertEqual([], collection_warnings(current))
        self.assertEqual({}, detect_changes(previous['SK7'][PAGE], current['SK7'][PAGE]))

    def test_unverified_empty_body_still_warns(self):
        previous = {'SK7': {PAGE: {'main_content': '이전 본문'}}}
        current = {'SK7': {PAGE: {'main_content': '', '_image_collection_error': 'timeout'}}}
        self.assertEqual(1, len(preserve_unavailable_fields(current, previous)))
        self.assertEqual('이전 본문', current['SK7'][PAGE]['main_content'])

    def test_hidden_notice_is_same_as_expanded_notice(self):
        template = '<div class="bbs-view"><div class="view-body"><div class="eventCont"><img src="/campaign.png"><div class="notice" {hidden}>상품권 5만원 지급, 10월 31일까지 개통</div></div></div></div>'
        results = []
        for hidden in ['', 'style="display:none" aria-hidden="true"']:
            driver = Mock(page_source=template.format(hidden=hidden))
            with patch('content_extractor.wait_and_expand', return_value=0):
                results.append(extract_page_content(driver, PAGE))
        self.assertEqual(results[0]['notice'], results[1]['notice'])
        self.assertIn('5만원', results[1]['notice'])
        self.assertEqual('image_only', results[1]['_body_status'])

    def test_same_accordion_clicked_only_once_across_strategies(self):
        driver, element = Mock(), Mock()
        element.id = 'one-panel'
        element.text = '유의사항'
        element.is_displayed.return_value = True
        element.get_attribute.side_effect = lambda name: {'aria-expanded': 'false', 'class': 'accordion', 'data-accordion': ''}.get(name)
        element.find_element.return_value.get_attribute.return_value = ''
        driver.find_elements.side_effect = lambda by, selector: [] if selector == 'summary' else [element]
        with patch('content_extractor._safe_click', return_value=True) as click:
            self.assertEqual(1, _click_accordions(driver))
        click.assert_called_once_with(driver, element)

    def test_open_accordion_is_not_toggled(self):
        element = Mock()
        element.get_attribute.return_value = 'true'
        self.assertFalse(_safe_click(Mock(), element))
        element.click.assert_not_called()

    def test_slack_includes_every_company_on_separate_lines(self):
        before = {name: {'u': {'title': name}} for name in ['U+', 'SK7', '헬로', '스카이', 'KTM', 'SKT']}
        after = copy.deepcopy(before)
        for events in after.values():
            events['u']['title'] += ' 변경'
        total, _, details = parser.calculate_changes(after, before)
        text = parser.format_slack_report(total, details, {})
        self.assertIn('총 6건 변동', text)
        for name in before:
            self.assertIn('\n• ' + name + ': 1건 (수정 1건)', text)
        self.assertNotIn('|보기>', text)
        self.assertNotIn('외 ', text)


if __name__ == '__main__':
    unittest.main()

