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

    def test_new_event_not_misreported_as_notice_edit(self):
        self.assertEqual({}, calculate_notice_diff(self.snap('상품권 5만원'), {'회사': {}}))


if __name__ == '__main__':
    unittest.main()
