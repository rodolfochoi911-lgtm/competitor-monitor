import unittest
from unittest.mock import Mock
from content_extractor import _safe_click


class ActualSk7ToggleTests(unittest.TestCase):
    def test_button_has_no_aria_state_but_close_title(self):
        element = Mock()
        element.tag_name = 'button'
        element.get_attribute.side_effect = {'class': 'btn btn-toggle', 'title': '닫기'}.get
        self.assertFalse(_safe_click(Mock(), element))
        element.click.assert_not_called()

    def test_expanded_state_on_sibling_panel(self):
        element = Mock()
        element.tag_name = 'button'
        element.get_attribute.side_effect = {'class': 'btn btn-toggle'}.get
        element.find_element.return_value.get_attribute.side_effect = {'aria-expanded': 'true'}.get
        self.assertFalse(_safe_click(Mock(), element))
        element.click.assert_not_called()

    def test_accordion_container_is_not_a_button(self):
        element = Mock()
        element.tag_name = 'div'
        element.get_attribute.side_effect = {'data-accordion': 'true'}.get
        self.assertFalse(_safe_click(Mock(), element))
        element.click.assert_not_called()
