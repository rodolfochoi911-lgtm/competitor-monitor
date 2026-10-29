# SKT / UMobile / Skylife stability

The October 29 audit found that KTM/Hello's body fix did not cover these collectors. SKT's body still depended on notice visibility; UMobile discarded accessibility prices because their class contained `blind`; Skylife read immediately after eager refresh and could save an application error heading as an event title.

## Scoped bodies

- SKT: read `.event-detail-content`, exclude verified notice accordions and personalized dialogs. Preserve campaign copy and accessibility image descriptions independently of visibility. Actual terms remain in the verified notice channel.
- UMobile: read `main .sub-conts.template`, covering default, USIM and special templates. Keep price descriptions and repeated contractual amounts. Exclude official notice sections, related events, dialogs and countdown/header controls. Keep the actual date range as `event_period` so header cleanup cannot hide period edits.
- The new body version establishes a clean extraction baseline without reporting the scope correction as a promotion edit.

## Skylife readiness and failures

- Every initial read and refresh waits for the actual populated promotion iframe. Open campaign Radix accordions one at a time and re-query after each React state update. Read the final outer panel tree once, with nested section names retained; do not append parent text repeatedly as children open.
- Use only campaign regions associated with its iframe, excluding notification regions and site navigation. Wait for complete panel mappings and stable text. Image campaigns without textual terms remain unconfirmed, rather than being treated as healthy empty terms or server errors.
- Reject application error pages and repeated extraction exceptions. Never reuse an earlier successful attempt after the final attempt raises.
- Detail failures are isolated by event URL. Retain the last baseline for recovery, keep the failed URL present to prevent false termination, skip it in event/notice changes and fresh notice exports, and surface a company-level failure count. Other events can still report genuine changes.
- Recovering an old application-error title is not a promotion edit. Normal titles and subsequent policy changes are compared normally.

`scripts/check_skylife_content.py` runs the actual extractor against explicit URLs in a separate headless browser, saves attempts/DOM evidence, and sends no notifications. Unit tests cover visibility invariance, actual price/period/benefit edits, complete nested reads, application errors, final-attempt failures and per-event baseline recovery. Historical raw snapshots remain unchanged.
