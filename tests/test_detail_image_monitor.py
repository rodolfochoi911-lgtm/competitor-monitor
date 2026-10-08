import hashlib
import unittest
from unittest.mock import Mock

from detail_image_monitor import extract_detail_image_urls, hash_detail_images
from monitor_core import detect_changes, preserve_unavailable_fields


PAGE = "https://www.sk7mobile.com/bnef/event/eventIngView.do?cntId=abc"
HTML = """<header><img src="/logo.png"></header>
<div id="ct"><section class="evt_cont">
<img src="/event/banner.png">
<img data-src="/event/benefit.png" src="data:image/gif;base64,AAAA">
<div style="background-image: url('/event/footer.png')"></div>
</section></div>"""


class FakeResponse:
    def __init__(self, content, content_type="image/png"):
        self.content = content
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=65536):
        yield self.content

    def close(self):
        pass


class FakeSession:
    def __init__(self, images):
        self.images = images

    def get(self, url, **kwargs):
        value = self.images[url]
        if isinstance(value, Exception):
            raise value
        return FakeResponse(value)


class DetailImageTests(unittest.TestCase):
    def test_only_detail_images_with_lazy_and_background_sources(self):
        urls = extract_detail_image_urls(HTML, PAGE)
        self.assertEqual([
            "https://www.sk7mobile.com/event/banner.png",
            "https://www.sk7mobile.com/event/benefit.png",
            "https://www.sk7mobile.com/event/footer.png",
        ], urls)
        self.assertNotIn("https://www.sk7mobile.com/logo.png", urls)

    def test_same_url_new_image_bytes_changes_hash(self):
        url = "https://www.sk7mobile.com/event/banner.png"
        html = '<main><img src="/event/banner.png"></main>'
        first, warning = hash_detail_images(html, PAGE, FakeSession({url: b'old'}))
        self.assertIsNone(warning)
        second, warning = hash_detail_images(html, PAGE, FakeSession({url: b'new'}))
        self.assertIsNone(warning)
        self.assertEqual([hashlib.sha256(b'old').hexdigest()], first)
        self.assertIn('detail_image_hashes', detect_changes(
            {'detail_image_hashes': first}, {'detail_image_hashes': second}))
        self.assertEqual({}, detect_changes({}, {'detail_image_hashes': second}))

    def test_failed_download_never_overwrites_baseline(self):
        import requests
        url = "https://www.sk7mobile.com/event/banner.png"
        html = '<main><img src="/event/banner.png"></main>'
        hashes, warning = hash_detail_images(
            html, PAGE, FakeSession({url: requests.Timeout("timeout")}))
        self.assertIsNone(hashes)
        self.assertIn('확인 실패', warning)
        old = {'A': {'u': {'detail_image_hashes': ['old_digest']}}}
        current = {'A': {'u': {'title': '행사'}}}
        warnings = preserve_unavailable_fields(current, old, 'old.json')
        self.assertEqual(['old_digest'], current['A']['u']['detail_image_hashes'])
        self.assertEqual(1, len(warnings))


if __name__ == '__main__':
    unittest.main()
