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
from monitor_core import prepare_partial_snapshot, successful_snapshot, snapshot_warnings


class PromotionRegressionTests(unittest.TestCase):
    def test_dashboard_imports_recover_cached_predeployment_module(self):
        import monitor_core
        for page in (Path('Home.py'), Path('pages/2_🚨_프로모션 변경 리포트.py')):
            with self.subTest(page=str(page)):
                del monitor_core.snapshot_warnings
                del monitor_core.comparison_snapshots
                prefix = page.read_text(encoding='utf-8').split('import streamlit as st', 1)[0]
                namespace = {}
                exec(compile(prefix, str(page), 'exec'), namespace)
                self.assertEqual({}, namespace['comparison_snapshots']({}, {})[0])
                self.assertEqual([], namespace['snapshot_warnings']({}, 'missing/data_test.json'))

    def test_tdirect_list_closes_offer_before_reading_event_cards(self):
        import main
        driver = Mock()
        close = Mock()
        close.is_displayed.side_effect = [True, False]
        order = []
        close.click.side_effect = lambda: order.append('close')
        def find(by, selector):
            if selector == '#trgtSupmOfferPop #offerPopClose':
                return [close]
            if selector.startswith('.event-list'):
                order.append('events')
                return [Mock()]
            return []
        driver.find_elements.side_effect = find
        with patch.object(main, '_dismiss_detail_alert'):
            main.open_tdirect_list(driver, 'https://shop.tworld.co.kr/exhibition/submain')
        self.assertEqual(['close', 'events'], order)
        driver.execute_script.assert_not_called()

    def test_tdirect_hidden_offer_is_not_clicked(self):
        import main
        driver = Mock()
        close = Mock()
        close.is_displayed.return_value = False
        driver.find_elements.side_effect = lambda by, selector: [close] if selector.startswith('#trgt') else []
        self.assertEqual(0, main.dismiss_tdirect_promotions(driver))
        close.click.assert_not_called()

    def test_tdirect_list_excludes_popup_and_header_links(self):
        import main
        driver = Mock()
        driver.page_source = '''<a href="/event/header"><img src="header.png"></a>
        <section id="trgtSupmOfferPop"><a href="/event/offer"><img src="offer.png"></a></section>
        <ul class="event-list"><li class="event-item"><a href="/nf/index_nf_yp_plan.html?exhibitionId=P00000525"><img src="roulette.png" alt="럭키 룰렛"></a></li></ul>'''
        with patch.object(main.time, 'sleep'):
            targets = main.extract_list_with_thumbnails(driver, 'SKT 다이렉트', ['event', 'plan'], base_url='https://shop.tworld.co.kr')
        self.assertEqual(['https://shop.tworld.co.kr/nf/index_nf_yp_plan.html?exhibitionId=P00000525'], list(targets))

    def test_tdirect_list_checks_delayed_popup_when_cards_arrive(self):
        import main
        driver = Mock()
        waiter = Mock()
        driver.find_elements.return_value = [Mock()]
        def wait_until(predicate):
            return predicate(driver)
        waiter.until.side_effect = wait_until
        with patch.object(main, '_dismiss_detail_alert'), patch.object(main, 'dismiss_tdirect_promotions') as dismiss, patch.object(main, 'WebDriverWait', return_value=waiter):
            main.open_tdirect_list(driver, 'https://shop.tworld.co.kr/exhibition/submain')
        dismiss.assert_called_once_with(driver)

    def test_tdirect_retries_blocking_alert_before_extracting(self):
        import main
        from selenium.common.exceptions import UnexpectedAlertPresentException, NoAlertPresentException
        driver = Mock()
        driver.find_elements.return_value = []
        driver.get.side_effect = [UnexpectedAlertPresentException('popup'), None]
        driver.current_url = 'https://shop.tworld.co.kr/exhibition/view?exhibitionId=P00000525'
        driver.execute_script.return_value = True
        with patch.object(main, '_dismiss_detail_alert') as dismiss:
            main.open_detail_page(driver, 'https://shop.tworld.co.kr/nf/index_nf_yp_plan.html?exhibitionId=P00000525', 'SKT 다이렉트')
        self.assertEqual(2, driver.get.call_count)
        self.assertGreaterEqual(dismiss.call_count, 2)

    def test_tdirect_resource_timeout_uses_ready_detail_body(self):
        import main
        from selenium.common.exceptions import TimeoutException
        driver = Mock()
        driver.find_elements.return_value = []
        driver.get.side_effect = TimeoutException('slow resource')
        driver.current_url = 'https://shop.tworld.co.kr/exhibition/view?exhibitionId=P00000525'
        driver.execute_script.return_value = True
        with patch.object(main, '_dismiss_detail_alert'):
            main.open_detail_page(driver, 'https://shop.tworld.co.kr/nf/index_nf_yp_plan.html?exhibitionId=P00000525', 'SKT 다이렉트')
        self.assertEqual(1, driver.get.call_count)

    def test_tdirect_does_not_collect_empty_netfunnel_shell(self):
        import main
        from selenium.common.exceptions import TimeoutException
        driver = Mock()
        driver.find_elements.return_value = []
        driver.current_url = 'https://shop.tworld.co.kr/nf/index_nf_yp_plan.html?exhibitionId=P00000525'
        waiter = Mock()
        def wait_until(predicate):
            self.assertFalse(predicate(driver))
            raise TimeoutException('queue not finished')
        waiter.until.side_effect = wait_until
        with patch.object(main, '_dismiss_detail_alert'), patch.object(main, 'WebDriverWait', return_value=waiter):
            with self.assertRaisesRegex(RuntimeError, 'TimeoutException'):
                main.open_detail_page(driver, driver.current_url, 'SKT 다이렉트')
        self.assertEqual(2, driver.get.call_count)

    def test_notice_expander_never_clicks_popup_or_navigation(self):
        from content_extractor import _safe_click, _is_inline_expander
        for attrs in [{'href': 'https://tdirect-event.co.kr/promotion/roulette-max'},
                      {'target': '_blank'}, {'onclick': 'window.open("/popup")'},
                      {'data-toggle': 'modal'}]:
            element = Mock()
            element.get_attribute.side_effect = attrs.get
            driver = Mock()
            self.assertFalse(_safe_click(driver, element))
            driver.execute_script.assert_not_called()
            element.click.assert_not_called()
        element = Mock()
        element.get_attribute.side_effect = {'href': '#notice', 'aria-expanded': 'false'}.get
        self.assertTrue(_is_inline_expander(element))

    def test_partial_failure_publishes_success_without_false_terminations(self):
        previous = {'A': {'old': {'title': 'old', 'notice': '3만원 지급'}},
                    'B': {'old': {'title': 'old', 'notice': '5만원 지급'}}}
        snapshot = prepare_partial_snapshot({'A': {}, 'B': {'new': {'title': 'new', 'notice': '7만원 지급'}}},
                                            previous, {'A': 'timeout'})
        validate_snapshot(snapshot, previous)
        self.assertEqual({'B'}, set(successful_snapshot(snapshot)))
        self.assertEqual({'B'}, set(parser.calculate_changes(snapshot, previous)[1]))
        self.assertEqual({'B'}, set(calculate_notice_diff(snapshot, previous)))
        self.assertNotIn('_collection_error', previous['A']['old'])
        self.assertEqual(1, len(collection_warnings(snapshot)))
        # Consecutive failures retain the baseline; recovery compares to it.
        again = prepare_partial_snapshot({'B': snapshot['B']}, snapshot, {'A': 'timeout again'})
        recovered = {'A': previous['A'], 'B': snapshot['B']}
        self.assertEqual(0, parser.calculate_changes(recovered, again)[0])
        self.assertEqual([], collection_warnings(recovered))

    def test_all_failed_even_with_previous_data_does_not_publish(self):
        with self.assertRaises(RuntimeError):
            prepare_partial_snapshot({'A': {}}, {'A': {'u': {'title': 'old'}}}, {'A': 'timeout'})

    def test_partial_run_reaches_parser_and_exports_only_successful_companies(self):
        import main
        import pandas as pd
        names = ['SKT 다이렉트', 'KTM 모바일', 'U+ 유모바일', '스카이라이프', '헬로모바일', 'SK 7세븐모바일']
        previous = {name: {'old': {'title': name, 'notice': '상품권 30,000원을 지급합니다.'}} for name in names}
        def crawl(driver, company):
            if company['name'] == names[0]:
                raise RuntimeError('detail timeout')
            return {'new': {'title': company['name'], 'notice': '상품권 50,000원을 지급합니다.'}}
        with tempfile.TemporaryDirectory() as temp:
            old_cwd = os.getcwd()
            try:
                os.chdir(temp)
                Path('data').mkdir()
                Path('data/data_20261005_000000.json').write_text(json.dumps(previous), encoding='utf-8')
                with patch.object(main, 'DATA_DIR', 'data'), patch.object(main, 'FILE_TIMESTAMP', '20261006_000000'), patch.object(main, 'setup_driver', return_value=Mock()), patch.object(main, 'crawl_site_logic', side_effect=crawl):
                    main.main()
                with patch.object(parser, 'send_slack_report') as report:
                    parser.run_parser()
                for filename in ['dashboard_latest.csv', 'notices_latest.csv']:
                    df = pd.read_csv(Path('data', filename))
                    self.assertEqual(set(names[1:]), set(df['company']))
                self.assertTrue(any(names[0] in warning for warning in report.call_args.args[3]))
                self.assertFalse(any(names[0] in detail for detail in report.call_args.args[1]))
            finally:
                os.chdir(old_cwd)

    def test_first_run_failure_is_reported_without_previous_baseline(self):
        with tempfile.TemporaryDirectory() as temp:
            snapshot = {'B': {'u': {'title': 'success'}}}
            path = Path(temp, 'data_20261006_000000.json')
            Path(temp, 'collection_status_20261006_000000.json').write_text(json.dumps({'A': 'timeout'}), encoding='utf-8')
            self.assertIn('A:', snapshot_warnings(snapshot, path)[0])

    def test_body_benefit_change_is_an_event_change(self):
        delta = detect_changes(
            {'title': '행사', 'img': 'same', 'main_content': '3만원 지급'},
            {'title': '행사', 'img': 'same', 'main_content': '5만원 지급'})
        self.assertIn('main_content', delta)

    def test_body_noise_only_is_not_an_event_change(self):
        self.assertEqual({}, detect_changes(
            {'title': '행사', 'main_content': '혜택 유지\n조회수 123\n검색일 2026-10-07'},
            {'title': '행사', 'main_content': '혜택 유지\n조회수 456\n검색일 2026-10-08'}))

    def test_formatting_only_body_change_is_ignored(self):
        self.assertEqual({}, detect_changes({'main_content': '<p>3만원 지급</p>'},
                                             {'main_content': '3만원  지급'}))

    def test_amount_and_date_variants_are_preserved(self):
        items = {'url1': {'title': '행사', 'notice': '가입 고객에게 상품권 30,000원을 지급합니다.\n가입 고객에게 상품권 50,000원을 지급합니다.\n2026년 9월 10일까지 가입한 고객에게 지급합니다.\n2026년 9월 20일까지 가입한 고객에게 지급합니다.'}}
        self.assertEqual(4, len(parser.collect_unique_notices(items)))

    def test_period_only_notice_edit_is_detected_with_source(self):
        url = 'https://www.sk7mobile.com/bnef/event/eventIngView.do?cntId=abc'
        old = {'SK 7세븐모바일': {url: {'title': '가입 혜택', 'notice': '혜택 신청기간은 2026년 10월 31일까지입니다.'}}}
        new = {'SK 7세븐모바일': {url: {'title': '가입 혜택', 'notice': '혜택 신청기간은 2026년 11월 30일까지입니다.'}}}
        diff = calculate_notice_diff(new, old)['SK 7세븐모바일']
        self.assertEqual(['혜택 신청기간은 2026년 11월 30일까지입니다.'], diff['added'])
        self.assertEqual(['혜택 신청기간은 2026년 10월 31일까지입니다.'], diff['removed'])
        self.assertEqual(url, diff['added_sources'][diff['added'][0]]['url'])

    def test_image_hash_baseline_and_same_url_replacement(self):
        base = {'title': '행사', 'img': '/thumbnail.png', 'main_content': '동일'}
        after = dict(base, detail_image_hashes=['new_hash'])
        self.assertEqual({}, detect_changes(base, after))
        changed = detect_changes(dict(base, detail_image_hashes=['old_hash']), after)
        self.assertIn('detail_image_hashes', changed)

    def test_slack_push_includes_image_event_url(self):
        old = {'A': {'https://example.com/event': {'title': '행사', 'detail_image_hashes': ['a']}}}
        new = {'A': {'https://example.com/event': {'title': '행사', 'detail_image_hashes': ['b']}}}
        count, _, details = parser.calculate_changes(new, old)
        self.assertEqual(1, count)
        with patch.object(parser, 'slack_webhook_url', 'https://example.invalid'), patch.object(parser.requests, 'post') as post:
            parser.send_slack_report(count, details, {})
            text = post.call_args.kwargs['json']['text']
            self.assertIn('상세 이미지 1개', text)
            self.assertIn('https://example.com/event', text)

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

