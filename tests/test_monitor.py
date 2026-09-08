import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

import parser
from monitor_core import detect_changes, calculate_notice_diff, validate_snapshot
from scripts.run_timing import timing_report
from datetime import datetime, timezone


class PromotionRegressionTests(unittest.TestCase):
    def test_body_only_change_is_detected(self):
        self.assertIn('main_content', detect_changes(
            {'title': '행사', 'img': 'same', 'main_content': '3만원 지급'},
            {'title': '행사', 'img': 'same', 'main_content': '5만원 지급'}))

    def test_formatting_only_body_change_is_ignored(self):
        self.assertEqual({}, detect_changes({'main_content': '<p>3만원 지급</p>'},
                                             {'main_content': '3만원  지급'}))

    def test_amount_and_date_variants_are_preserved(self):
        items = {'url1': {'title': '행사', 'notice': '가입 고객에게 상품권 30,000원을 지급합니다.\n가입 고객에게 상품권 50,000원을 지급합니다.\n2026년 9월 10일까지 가입한 고객에게 지급합니다.\n2026년 9월 20일까지 가입한 고객에게 지급합니다.'}}
        self.assertEqual(4, len(parser.collect_unique_notices(items)))

    def test_identical_notice_keeps_both_event_sources(self):
        text = '가입 고객에게 상품권 30,000원을 지급합니다.'
        result = parser.collect_unique_notices({'a': {'notice': text}, 'b': {'notice': text}})
        self.assertEqual({'a', 'b'}, {x['url'] for x in result})

    def test_notice_move_between_events_is_not_hidden(self):
        old = {'company': {'a': {'notice': '5만원 지급'}, 'b': {'notice': ''}}}
        new = {'company': {'a': {'notice': ''}, 'b': {'notice': '5만원 지급'}}}
        diff = calculate_notice_diff(new, old)['company']
        self.assertEqual(1, len(diff['added']))
        self.assertEqual(1, len(diff['removed']))

    def test_notice_only_notification_is_not_no_change(self):
        with patch.object(parser, 'slack_webhook_url', 'https://example.invalid'), patch.object(parser.requests, 'post') as post:
            parser.send_slack_report(0, [], {'A': {'added': ['3만원 → 5만원'], 'removed': []}})
            text = post.call_args.kwargs['json']['text']
            self.assertNotIn('특이사항 없음', text)
            self.assertIn('3만원 → 5만원', text)

    def test_empty_company_is_rejected(self):
        with self.assertRaises(RuntimeError):
            validate_snapshot({'A': {}})

    def test_missing_previous_company_is_rejected(self):
        with self.assertRaises(RuntimeError):
            validate_snapshot({'A': {'u': {}}}, {'A': {'u': {}}, 'B': {'u': {}}})

    def test_lost_notice_is_rejected(self):
        with self.assertRaises(RuntimeError):
            validate_snapshot({'A': {'u': {'notice': ''}}}, {'A': {'u': {'notice': '지급 조건'}}})

    def test_failed_crawl_does_not_write_snapshot(self):
        import main
        with tempfile.TemporaryDirectory() as temp, patch.object(main, 'DATA_DIR', temp), patch.object(main, 'setup_driver', return_value=Mock()), patch.object(main, 'crawl_site_logic', side_effect=RuntimeError('timeout')):
            with self.assertRaises(RuntimeError):
                main.main()
            self.assertEqual([], list(Path(temp).glob('data_*.json')))

    def test_retry_does_not_duplicate_csv_rows(self):
        import pandas as pd
        with tempfile.TemporaryDirectory() as temp:
            latest, history = str(Path(temp) / 'latest.csv'), str(Path(temp) / 'history.csv')
            data = pd.DataFrame([{'date': '2026-09-08', 'company': 'A'}])
            parser.safe_save(data.copy(), latest, history, ['date', 'company'])
            parser.safe_save(data.copy(), latest, history, ['date', 'company'])
            self.assertEqual(1, len(pd.read_csv(history)))

    def test_schedule_utc_to_kst(self):
        text = timing_report('7 21 * * *', datetime(2026, 9, 7, 23, 24, 40, tzinfo=timezone.utc))
        self.assertIn('2026-09-08 06:07', text)
        self.assertIn('137.7분', text)


if __name__ == '__main__':
    unittest.main()
