import unittest

import pandas as pd

from scripts.monitor_crawler import aggregate_metrics_by_source, count_brand_mentions


class MarketSourceMetricsTests(unittest.TestCase):
    def setUp(self):
        self.posts = pd.DataFrame([
            {
                'source': 'ppomppu',
                'title': '유모바일 추천인 문의',
                'link': 'https://example.com/1',
                'views': 10,
                'comments': 1,
            },
            {
                'source': 'dc',
                'title': '유모바일 개통 후기',
                'link': 'https://example.com/2',
                'views': 20,
                'comments': 2,
            },
            {
                'source': 'dc',
                'title': '프리티 셀프개통 완료',
                'link': 'https://example.com/3',
                'views': 30,
                'comments': 3,
            },
        ])

    def test_source_brand_counts_sum_to_legacy_total(self):
        legacy = count_brand_mentions(self.posts)
        by_source, _ = aggregate_metrics_by_source(self.posts)

        for brand, total in legacy.items():
            self.assertEqual(
                total,
                by_source['ppomppu'][brand] + by_source['dc'][brand],
            )

    def test_source_keyword_counts_are_kept_separate(self):
        _, keywords = aggregate_metrics_by_source(self.posts)

        self.assertEqual(1, keywords['ppomppu']['유모바일'])
        self.assertEqual(1, keywords['dc']['유모바일'])
        self.assertEqual(1, keywords['dc']['프리티'])

    def test_known_sources_are_emitted_when_one_is_empty(self):
        only_ppomppu = self.posts[self.posts['source'] == 'ppomppu']
        by_source, keywords = aggregate_metrics_by_source(only_ppomppu)

        self.assertIn('dc', by_source)
        self.assertIn('dc', keywords)
        self.assertEqual({}, keywords['dc'])
        self.assertTrue(all(count == 0 for count in by_source['dc'].values()))


if __name__ == '__main__':
    unittest.main()
