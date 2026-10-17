"""U+ UMobile countdown and event-code change regressions."""
import unittest
from unittest.mock import Mock, patch

from content_extractor import extract_page_content
from monitor_core import calculate_notice_diff, detect_changes
from promotion_noise import stable_text
import parser


URL = "https://www.uplusumobile.com/event-benefit/event/ongoing/12684"
COMPANY = "U+ 유모바일"


class UMobileEventCodeTests(unittest.TestCase):
    def test_numeric_remaining_counter_is_removed_even_with_split_markup(self):
        old = ("진행중인 이벤트 22GB+ 요금제 월 17,500원 -> 0원! "
               "2026-10-15 2026-10-19 공유아이콘 10 남았어요!")
        new = old.replace("10 남았어요!", "45 남았어요!")
        self.assertEqual(stable_text(old), stable_text(new))
        self.assertIn("2026-10-15", stable_text(new))
        self.assertNotIn("남았어요", stable_text(new))
        self.assertNotIn("공유아이콘", stable_text(new))
        html = ("<p>행사기간 2026-10-15 ~ 2026-10-19</p>"
                "<div>공유아이콘 <span>45</span><span>남았어요!</span></div>")
        self.assertEqual("행사기간 2026-10-15 ~ 2026-10-19", stable_text(html))
        self.assertEqual({}, detect_changes({"main_content": old}, {"main_content": new}))

    def test_actual_event_dates_still_trigger_change(self):
        old = "2026-10-02 ~ 2026-10-14 공유아이콘 10 남았어요!"
        new = "2026-10-15 ~ 2026-10-19 공유아이콘 45 남았어요!"
        self.assertIn("main_content", detect_changes({"main_content": old}, {"main_content": new}))

    def test_short_event_code_is_retained_and_detected(self):
        before_code = "이벤트코드 : 가을특가"
        after_code = "이벤트코드 : 해피할로윈"
        old = {COMPANY: {URL: {"title": "22GB+ 할인", "notice": before_code}}}
        new = {COMPANY: {URL: {"title": "22GB+ 할인", "notice": after_code}}}
        notices = parser.collect_unique_notices(new[COMPANY], company=COMPANY)
        self.assertEqual([after_code], [item["notice"] for item in notices])
        change = calculate_notice_diff(new, old)[COMPANY]
        self.assertEqual([after_code], change["added"])
        self.assertEqual([before_code], change["removed"])
        self.assertEqual(URL, change["added_sources"][after_code]["url"])
        slack = parser.format_slack_report(0, [], {COMPANY: change})
        self.assertIn("코드 가을특가→해피할로윈", slack)
        self.assertIn(f"<{URL}|보기>", slack)

    def test_event_code_and_period_survive_umobile_notice_extraction(self):
        driver = Mock()
        driver.page_source = (
            '<main><div class="event-detail"><h2>유의사항</h2>'
            '<section class="event-terms">'
            '<button>이벤트코드 혜택 유의사항</button>'
            '<ul><li>이벤트코드 : 해피할로윈</li>'
            '<li>이벤트 기간 : 10월 15일 ~ 10월 19일까지</li></ul>'
            '</section></div></main>'
        )
        with patch("content_extractor.wait_and_expand", return_value=1):
            result = extract_page_content(driver, URL)
        self.assertIn("이벤트코드 : 해피할로윈", result["notice"])
        self.assertIn("10월 19일", result["notice"])
        self.assertIn("이벤트코드 : 해피할로윈",
                      [item["notice"] for item in parser.collect_unique_notices(
                          {URL: {"title": "22GB+", "notice": result["notice"]}},
                          company=COMPANY)])


if __name__ == "__main__":
    unittest.main()
