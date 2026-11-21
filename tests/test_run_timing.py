import unittest
from datetime import datetime, timezone
from scripts.run_timing import timing_report


class TimingTests(unittest.TestCase):
    def test_external_original_slot_survives_start_delay(self):
        report = timing_report('', datetime(2026, 11, 17, 21, 10, tzinfo=timezone.utc),
                               '2026-11-17T21:07:00.000Z', 'cloudflare')
        self.assertIn('Cloudflare', report)
        self.assertIn('2026-11-18 06:07', report)
        self.assertIn('3.0분', report)

    def test_daily_native_slot_uses_previous_day(self):
        report = timing_report('45 18 * * *', datetime(2026, 11, 17, 2, 4, tzinfo=timezone.utc))
        self.assertIn('2026-11-17 03:45', report)
        self.assertIn('439.0분', report)

    def test_invalid_external_slot_fails(self):
        now = datetime(2026, 11, 17, 21, 10, tzinfo=timezone.utc)
        for slot in ['bad', '2026-11-17T21:07:00', '2026-11-18T21:07:00Z']:
            with self.subTest(slot=slot), self.assertRaises(ValueError):
                timing_report('', now, slot)

    def test_manual_run_is_not_reported_as_scheduled(self):
        self.assertIn('수동 실행', timing_report('', datetime.now(timezone.utc)))


if __name__ == '__main__':
    unittest.main()
