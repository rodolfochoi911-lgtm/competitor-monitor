import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import parser


class EmailReportTests(unittest.TestCase):
    def prepare(self, total=0, details=None, notices=None, warnings=None):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp, 'outputs')
            directory = Path(temp, 'mail')
            with patch.dict(os.environ, {'PROMOTION_MAIL_DIR': str(directory),
                                         'GITHUB_OUTPUT': str(output)}), \
                    patch.object(parser.requests, 'post') as post:
                parser.prepare_email_report(total, details or [], notices or {}, warnings)
                post.assert_not_called()
            return (Path(directory, 'subject.txt').read_text(encoding='utf-8'),
                    Path(directory, 'body.txt').read_text(encoding='utf-8'),
                    output.read_text(encoding='utf-8'))

    def test_unchanged_run_does_not_send(self):
        subject, body, output = self.prepare()
        self.assertIn('특이사항 없음', subject)
        self.assertIn('특이사항 없음', body)
        self.assertEqual('mail_needed=false\n', output)

    def test_event_change_preserves_summary_and_dashboard_link(self):
        subject, body, output = self.prepare(2, ['• A: 2건 (신규 1건, 수정 1건)'])
        self.assertIn('프로모션 2건 변동', subject)
        self.assertIn('신규 1건, 수정 1건', body)
        self.assertIn('대시보드: https://', body)
        self.assertNotIn('<https://', body)
        self.assertEqual('mail_needed=true\n', output)

    def test_notice_only_change_includes_every_company_and_source(self):
        notices = {name: {'added': ['상품권 5만원 지급'], 'removed': [],
                          'added_sources': {'상품권 5만원 지급': {'url': 'https://example.com/event'}}}
                   for name in ['A', 'B', 'C', 'D', 'E']}
        subject, body, output = self.prepare(notices=notices)
        self.assertIn('유의사항 5개 회사 변경', subject)
        for name in notices:
            self.assertIn(f'• {name}:', body)
        self.assertIn('보기: https://example.com/event', body)
        self.assertNotIn('특이사항 없음', body)
        self.assertEqual('mail_needed=true\n', output)

    def test_collection_warning_sends_without_changes(self):
        subject, body, output = self.prepare(warnings=['A: 수집 실패'])
        self.assertIn('수집 점검 1건', subject)
        self.assertIn('A: 수집 실패', body)
        self.assertIn('/competitor-monitor/actions', body)
        self.assertEqual('mail_needed=true\n', output)


if __name__ == '__main__':
    unittest.main()
