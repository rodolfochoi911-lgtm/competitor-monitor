import copy
import unittest
from unittest.mock import Mock
from notice_observation import collect_verified_notice
from monitor_core import preserve_unavailable_fields, calculate_notice_diff


class ObservationTests(unittest.TestCase):
    def snap(self, value, observation=None):
        event = {'title': '행사', 'notice': value}
        if observation:
            event['_notice_observation'] = observation
        return {'회사': {'url': event}}

    def test_two_partial_runs_never_promote_and_keep_changed_amount(self):
        previous = self.snap('상품권 5만원\n명의변경 시 지급 제외')
        for _ in range(2):
            current = self.snap('상품권 7만원')
            warnings = preserve_unavailable_fields(current, previous, 'baseline.json')
            event = current['회사']['url']
            self.assertEqual('상품권 5만원\n명의변경 시 지급 제외', event['notice'])
            self.assertEqual('상품권 7만원', event['_notice_pending']['observed'])
            self.assertTrue(warnings)
            self.assertEqual({}, calculate_notice_diff(current, previous))
            previous = current

    def test_recovery_reports_real_change_against_last_baseline(self):
        previous = self.snap('상품권 5만원\n명의변경 시 지급 제외')
        failed = self.snap('상품권 7만원')
        preserve_unavailable_fields(failed, previous)
        recovered = self.snap('상품권 7만원\n명의변경 시 지급 제외', {'status': 'verified'})
        preserve_unavailable_fields(recovered, failed)
        self.assertEqual(['상품권 7만원'], calculate_notice_diff(recovered, failed)['회사']['added'])

    def test_identical_incomplete_attempts_are_not_verified(self):
        extract = Mock(return_value={'notice': '상품권 7만원', '_notice_complete': False})
        result = collect_verified_notice(Mock(), 'url', extract)
        self.assertEqual('unconfirmed', result['_notice_observation']['status'])
        self.assertEqual(3, extract.call_count)

    def test_same_run_confirms_after_full_reload(self):
        driver = Mock()
        extract = Mock(return_value={'notice': '상품권 7만원', '_notice_complete': True, '_notice_scope': '.notice'})
        result = collect_verified_notice(driver, 'url', extract)
        self.assertEqual('verified', result['_notice_observation']['status'])
        driver.refresh.assert_called_once()
        self.assertEqual(2, len(result['_notice_observation']['attempts']))

    def test_oscillation_is_unconfirmed(self):
        extract = Mock(side_effect=[{'notice': n, '_notice_complete': True, '_notice_scope': '.notice'}
                                    for n in ['5만원', '7만원', '5만원']])
        self.assertEqual('unconfirmed', collect_verified_notice(Mock(), 'url', extract)['_notice_observation']['status'])

    def test_exclusion_removal_not_hidden_by_other_event(self):
        old = self.snap('상품권 5만원\n중복 지급 불가')
        old['회사']['other'] = copy.deepcopy(old['회사']['url'])
        new = copy.deepcopy(old)
        new['회사']['url']['notice'] = '상품권 5만원'
        self.assertIn('중복 지급 불가', calculate_notice_diff(new, old)['회사']['removed'])

    def test_repeated_clause_removal_within_event_is_not_hidden(self):
        old = self.snap('[혜택 A]\n명의변경 시 지급 제외\n[혜택 B]\n명의변경 시 지급 제외')
        new = self.snap('[혜택 A]\n명의변경 시 지급 제외\n[혜택 B]')
        self.assertIn('명의변경 시 지급 제외', calculate_notice_diff(new, old)['회사']['removed'])

    def test_new_event_not_misreported_as_notice_edit(self):
        self.assertEqual({}, calculate_notice_diff(self.snap('상품권 5만원'), {'회사': {}}))

    def test_first_partial_observation_does_not_become_baseline(self):
        current = self.snap('상품권 7만원', {'status': 'unconfirmed', 'version': 2})
        preserve_unavailable_fields(current, {})
        event = current['회사']['url']
        self.assertEqual('', event['notice'])
        self.assertEqual('상품권 7만원', event['_notice_pending']['observed'])
        self.assertNotIn('상품권 7만원', event['full_text'])

    def test_first_corrected_extraction_is_reviewable_not_silently_ignored(self):
        previous = self.snap('추가지원금 5만원')
        current = self.snap('룰렛 당첨 30만원', {'status': 'verified', 'version': 2})
        preserve_unavailable_fields(current, previous)
        delta = calculate_notice_diff(current, previous)['회사']
        self.assertTrue(delta['review_required'])
        self.assertIn('룰렛 당첨 30만원', delta['added'])

    def test_stable_recheck_does_not_repeat_correction_notice(self):
        previous = self.snap('룰렛 당첨 30만원', {'status': 'verified', 'version': 2})
        current = self.snap('룰렛 당첨 30만원', {'status': 'verified', 'version': 2})
        preserve_unavailable_fields(current, previous)
        self.assertNotIn('_notice_transition', current['회사']['url'])
        self.assertEqual({}, calculate_notice_diff(current, previous))

    def test_failed_first_upgrade_does_not_disguise_later_correction_as_policy(self):
        previous = self.snap('추가지원금 5만원')
        failed = self.snap('', {'status': 'unconfirmed', 'version': 2})
        preserve_unavailable_fields(failed, previous)
        recovered = self.snap('룰렛 당첨 30만원', {'status': 'verified', 'version': 2})
        preserve_unavailable_fields(recovered, failed)
        self.assertTrue(calculate_notice_diff(recovered, failed)['회사']['review_required'])


if __name__ == '__main__':
    unittest.main()
