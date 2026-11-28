"""Record the intended daily UTC slot and actual job time in the run summary."""
import os
from datetime import datetime, timedelta, timezone


def timing_report(cron, now, scheduled_at='', source=''):
    kst = timezone(timedelta(hours=9))
    if scheduled_at:
        expected = datetime.fromisoformat(scheduled_at.replace('Z', '+00:00'))
        if expected.tzinfo is None or expected > now:
            raise ValueError('Scheduled time must include a timezone and cannot be in the future')
        delay = (now - expected).total_seconds() / 60
        label = 'Cloudflare' if source == 'cloudflare' else '외부 호출'
        return (f"예약 경로: {label}\n"
                f"예정: {expected.astimezone(kst):%Y-%m-%d %H:%M} KST\n"
                f"작업 진단 시각: {now.astimezone(kst):%Y-%m-%d %H:%M:%S} KST\n"
                f"예정 대비 경과: {delay:.1f}분 (외부 호출 + 서버 대기 + 작업 준비 시간)")
    if not cron:
        return f"수동 실행: {now.astimezone(kst):%Y-%m-%d %H:%M:%S} KST"
    minute, hour, day, month, weekday = cron.split()
    if (day, month, weekday) != ('*', '*', '*'):
        raise ValueError('Only daily schedules are supported')
    expected = now.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    if expected > now:
        expected -= timedelta(days=1)
    delay = (now - expected).total_seconds() / 60
    return (f"예정: {expected.astimezone(kst):%Y-%m-%d %H:%M} KST\n"
            f"작업 진단 시각: {now.astimezone(kst):%Y-%m-%d %H:%M:%S} KST\n"
            f"예정 대비 경과: {delay:.1f}분 (예약 지연 + 작업 준비 시간 포함)\n"
            "GitHub schedule은 정시 실행을 보장하지 않습니다.")


if __name__ == '__main__':
    report = timing_report(os.environ.get('SCHEDULE_CRON', ''), datetime.now(timezone.utc),
                           os.environ.get('SCHEDULED_AT', ''), os.environ.get('SCHEDULE_SOURCE', ''))
    print(report)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as f:
            f.write('### 실행 시각\n\n' + report.replace('\n', '  \n') + '\n')

