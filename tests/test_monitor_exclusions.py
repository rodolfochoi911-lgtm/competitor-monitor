import copy
import unittest
from unittest.mock import Mock

from monitor_exclusions import EXCLUDED_EVENT_URLS, filter_snapshot, is_excluded_event
from monitor_core import collection_warnings, comparison_snapshots, calculate_notice_diff


class MonitorExclusionTests(unittest.TestCase):
    def test_reviewed_urls_and_tracking_variants_are_excluded(self):
        self.assertEqual(19, len(EXCLUDED_EVENT_URLS))
        for url in EXCLUDED_EVENT_URLS:
            with self.subTest(url=url):
                self.assertTrue(is_excluded_event(url))
                sep = '&' if '?' in url else '?'
                self.assertTrue(is_excluded_event(url + sep + 'utm_source=slack&page=2'))

    def test_neighbor_events_and_companies_remain_monitored(self):
        for url in (
            'https://www.ktmmobile.com/event/eventDetail.do?ntcartSeq=1853',
            'https://www.uplusumobile.com/event-benefit/event/ongoing/12642',
            'https://shop.tworld.co.kr/exhibition/view?exhibitionId=P00000326',
            'https://www.skylife.co.kr/event/new-campaign.do',
            'https://direct.lghellovision.net/event/viewEventDetailGuest.do?idxOfEvent=5552',
            'https://npay.sk7mobile.com/other.do',
        ):
            self.assertFalse(is_excluded_event(url), url)
        self.assertTrue(is_excluded_event(
            'https://shop.tworld.co.kr/exhibition/view?utm_source=test&exhibitionId=P00000513'))

    def test_exclusion_does_not_emit_termination_or_notice_removal(self):
        excluded = EXCLUDED_EVENT_URLS[0]
        kept = 'https://example.com/meaningful'
        old = {'company': {excluded: {'notice': '상품권 5만원 지급'}, kept: {'notice': '혜택'}}}
        original = copy.deepcopy(old)
        new = {'company': {kept: {'notice': '혜택'}}}
        current, previous = comparison_snapshots(new, old)
        self.assertEqual(set(current['company']), set(previous['company']))
        self.assertEqual({}, calculate_notice_diff(new, old))
        self.assertEqual(original, old)

    def test_excluded_warnings_disappear_but_new_failures_still_warn(self):
        data = {'company': {url: {'_notice_pending': {'changed': True, 'observed': '불완전'}}
                            for url in EXCLUDED_EVENT_URLS}}
        self.assertEqual([], collection_warnings(data))
        data['company']['https://example.com/new-failure'] = {'_retained_fields': {'notice': 'old'}}
        self.assertEqual(['company: 수집 확인 필요 (유의사항 1건)'], collection_warnings(data))
        self.assertEqual(1, len(filter_snapshot(data)['company']))

    def test_generic_crawler_skips_before_opening_detail(self):
        from main import visit_detail_pages
        driver = Mock()
        targets = {url: {} for url in EXCLUDED_EVENT_URLS}
        self.assertEqual({}, visit_detail_pages(driver, targets, 'KTM 모바일'))
        driver.get.assert_not_called()


if __name__ == '__main__':
    unittest.main()

