"""Non-notifying live validation of U+ UMobile event code extraction."""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.pop("SLACK_WEBHOOK_URL", None)

from main import setup_driver
from content_extractor import extract_page_content
from monitor_core import notice_lines
from promotion_noise import stable_text

URL = "https://www.uplusumobile.com/event-benefit/event/ongoing/12684"


def main():
    driver = setup_driver()
    try:
        driver.get(URL)
        data = extract_page_content(driver, URL)
    finally:
        driver.quit()

    body = data["main_content"]
    notice = data["notice"]
    code_lines = sorted(line for line in notice_lines(notice)
                        if re.search(r"이벤트\s*코드\s*[:：=]", line))
    print(f"U+ live extracted: body={len(body)} chars, notice={len(notice)} chars, "
          f"code_lines={code_lines!r}, countdown_left={'남았어요' in stable_text(body)}")
    assert body.strip(), "Live UMobile event body empty"
    assert notice.strip(), "Live UMobile event notice empty"
    assert code_lines, "U+ event code missing from extracted notice and notice filter"
    assert "해피할로윈" in "\n".join(code_lines), "Expected active event code missing"
    assert "남았어요" not in stable_text(body), "Countdown noise remains in normalized event body"
    print("PASS: live event code extracted and numeric/D-day countdown normalized")


if __name__ == "__main__":
    main()
