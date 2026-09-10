import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

import parser
from monitor_core import detect_changes, calculate_notice_diff, validate_snapshot, preserve_unavailable_fields, collection_warnings
from scripts.run_timing import timing_report
from datetime import datetime, timezone


class PromotionRegressionTests(unittest.TestCase):
    def test_body_noise_is_not_an_event_change(self):
        self.assertEqual({}, detect_changes(
            {'title': '행사', 'img': 'same', 'main_content': '3만원 지급'},
            {'title': '행사', 'img': 'same', 'main_content': '5만원 지급'}))

    def test_formatting_only_body_change_is_ignored(self):
        self.assertEqual({}, detect_changes({'main_content': '<p>3만원 지급</p>'},
                                             {'main_content': '3만원  지급'}))

    def test_amount_and_date_variants_are_preserved(self):
        items = {'url1': {'title': '행사', 'notice': '가입 고객에게 상품권 30,000원을 지급합니다.\n가입 고객에게 상품권 50,000원을 지급합니다.\n2026년 9월 10일까지 가입한 고객에게 지급합니다.\n2026년 9월 20일까지 가입한 고객에게 지급합니다.'}}
        self.assertEqual(2, len(parser.collect_unique_notices(items)))

    def test_identical_notice_keeps_both_event_sources(self):
        text = '가입 고객에게 상품권 30,000원을 지급합니다.'
        result = parser.collect_unique_notices({'a': {'notice': text}, 'b': {'notice': text}})
        self.assertEqual(1, len(result))

    def test_notice_move_between_events_is_not_a_change(self):
        old = {'company': {'a': {'notice': '5만원 지급'}, 'b': {'notice': ''}}}
        new = {'company': {'a': {'notice': ''}, 'b': {'notice': '5만원 지급'}}}
        self.assertEqual({}, calculate_notice_diff(new, old))

    def test_views_and_search_dates_are_excluded_from_notice_diff(self):
        old = {'A': {'u': {'notice': '조회수 123\n검색일 2026-09-09\n상품권 3만원 지급'}}}
        new = {'A': {'u': {'notice': '조회수 456\n검색일 2026-09-10\n상품권 3만원 지급'}}}
        self.assertEqual({}, calculate_notice_diff(new, old))

    def test_amount_notice_change_is_detected_without_event_label(self):
        old = {'A': {'u1': {'title': '행사명', 'notice': '상품권 3만원 지급'}}}
        new = {'A': {'u2': {'title': '다른 행사명', 'notice': '상품권 5만원 지급'}}}
        diff = calculate_notice_diff(new, old)['A']
        self.assertEqual(['상품권 5만원 지급'], diff['added'])
        self.assertEqual(['상품권 3만원 지급'], diff['removed'])

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

    def test_lost_notice_is_preserved_and_flagged(self):
        current = {'A': {'u': {'notice': ''}}}
        warnings = preserve_unavailable_fields(current, {'A': {'u': {'notice': '지급 조건'}}}, 'old.json')
        self.assertEqual('지급 조건', current['A']['u']['notice'])
        self.assertEqual({'notice': 'old.json'}, current['A']['u']['_retained_fields'])
        self.assertEqual(1, len(warnings))

    def test_reported_skt_empty_body_does_not_abort_or_fake_a_change(self):
        url = 'https://shop.tworld.co.kr/nf/index_nf_yp_plan.html?exhibitionId=P00000326'
        old = {'SKT 다이렉트': {url: {'title': '요금제', 'main_content': '이전 본문', 'notice': '기존 조건'}}}
        current = {'SKT 다이렉트': {url: {'title': '요금제', 'main_content': '', 'notice': '새 조건'}}}
        preserve_unavailable_fields(current, old, 'old.json')
        self.assertEqual('이전 본문', current['SKT 다이렉트'][url]['main_content'])
        self.assertEqual('새 조건', current['SKT 다이렉트'][url]['notice'])
        self.assertEqual({}, detect_changes(old['SKT 다이렉트'][url], current['SKT 다이렉트'][url]))
        self.assertFalse(calculate_notice_diff(current, old))

    def test_repeated_failure_keeps_original_provenance(self):
        previous = {'A': {'u': {'main_content': 'old', '_retained_fields': {'main_content': 'first.json'}}}}
        current = {'A': {'u': {'main_content': ''}}}
        preserve_unavailable_fields(current, previous, 'second.json')
        self.assertEqual('first.json', current['A']['u']['_retained_fields']['main_content'])

    def test_recovery_clears_warning(self):
        previous = {'A': {'u': {'main_content': 'old', '_retained_fields': {'main_content': 'first.json'}}}}
        current = {'A': {'u': {'main_content': 'new'}}}
        self.assertEqual([], preserve_unavailable_fields(current, previous, 'second.json'))
        self.assertEqual('new', current['A']['u']['main_content'])

    def test_warning_notification_does_not_claim_no_change(self):
        with patch.object(parser, 'slack_webhook_url', 'https://example.invalid'), patch.object(parser.requests, 'post') as post:
            parser.send_slack_report(0, [], {}, ['본문 이전 값 보존'])
            text = post.call_args.kwargs['json']['text']
            self.assertNotIn('특이사항 없음', text)
            self.assertIn('수집 확인 필요', text)

    def test_failed_crawl_does_not_write_snapshot(self):
        import main
        with tempfile.TemporaryDirectory() as temp, patch.object(main, 'DATA_DIR', temp), patch.object(main, 'setup_driver', return_value=Mock()), patch.object(main, 'crawl_site_logic', side_effect=RuntimeError('timeout')):
            with self.assertRaises(RuntimeError):
                main.main()
            self.assertEqual([], list(Path(temp).glob('data_*.json')))

    def test_complete_run_saves_other_companies_when_one_body_is_empty(self):
        import main
        companies = ['SKT 다이렉트', 'KTM 모바일', 'U+ 유모바일', '스카이라이프', '헬로모바일', 'SK 7세븐모바일']
        previous = {name: {'u': {'title': name, 'main_content': '이전 본문', 'notice': '이전 조건'}} for name in companies}
        def crawl(driver, company):
            return {'u': {'title': company['name'], 'main_content': '' if company['name'] == companies[0] else '갱신 본문', 'notice': '갱신 조건'}}
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, 'data_20260908_000000.json').write_text(json.dumps(previous), encoding='utf-8')
            with patch.object(main, 'DATA_DIR', temp), patch.object(main, 'FILE_TIMESTAMP', '20260908_010000'), patch.object(main, 'setup_driver', return_value=Mock()), patch.object(main, 'crawl_site_logic', side_effect=crawl):
                main.main()
            saved = json.loads(Path(temp, 'data_20260908_010000.json').read_text(encoding='utf-8'))
            self.assertEqual('이전 본문', saved[companies[0]]['u']['main_content'])
            self.assertEqual('갱신 본문', saved[companies[1]]['u']['main_content'])
            self.assertEqual(1, len(collection_warnings(saved)))

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

