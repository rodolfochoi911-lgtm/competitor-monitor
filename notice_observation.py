"""Keep raw attempts separate from the confirmed notice used for comparison."""
import hashlib
from datetime import datetime, timezone
from monitor_core import normalized_body


def collect_verified_notice(driver, url, extract, max_attempts=3):
    attempts = []
    result = {}
    for index in range(max_attempts):
        try:
            if index:
                driver.refresh()
            result = extract(driver, url)
            value = result.get('notice', '')
            attempts.append({'value': value, 'scope': result.get('_notice_scope', ''),
                             'complete': bool(result.get('_notice_complete')),
                             'observed_at': datetime.now(timezone.utc).isoformat()})
        except Exception as error:
            attempts.append({'value': '', 'complete': False, 'error': str(error)})
        if len(attempts) >= 2:
            a, b = attempts[-2:]
            if (a.get('complete') and b.get('complete') and a.get('scope') == b.get('scope')
                    and normalized_body(a['value']) == normalized_body(b['value'])):
                break
    verified = (len(attempts) >= 2 and attempts[-1].get('complete')
                and attempts[-2].get('complete')
                and attempts[-1].get('scope') == attempts[-2].get('scope')
                and normalized_body(attempts[-1]['value']) == normalized_body(attempts[-2]['value']))
    if not result:
        result = {'main_content': '', 'notice': '', 'full_text': ''}
    result['_notice_observation'] = {'version': 1, 'status': 'verified' if verified else 'unconfirmed',
                                     'attempts': attempts}
    return result


def retain_unconfirmed_notice(event, old, previous_snapshot):
    observation = event.get('_notice_observation')
    before, after = normalized_body(old.get('notice')), normalized_body(event.get('notice'))
    if observation is None and before == after:
        return False
    if observation is None:
        # Older collectors must never promote a repeated bad value by hash alone.
        observation = {'version': 1, 'status': 'unconfirmed',
                       'attempts': [{'value': event.get('notice', ''), 'complete': False}]}
        event['_notice_observation'] = observation
    if observation['status'] == 'verified':
        event.pop('_notice_pending', None)
        return False
    event['_notice_pending'] = {'baseline': old.get('notice', ''),
                               'observed': event.get('notice', ''),
                               'since': old.get('_notice_pending', {}).get('since') or previous_snapshot,
                               'changed': before != after}
    event.pop('_notice_candidate', None)
    return bool(before)
