# KTM / Hello campaign content stability (2026-10-29)

## Evidence

Seven production snapshots from October 26–29 were compared. Repeated KTM changes after deployment were primarily in `main_content`, not the verified notice. Examples alternated between `이벤트코드 'M' 입력 시 1만원 X 6개월` and `1.5만원 X 8개월`, or 5/10/20GB SOLO benefits across several events. These summaries coexist as rate-specific variants in the campaign DOM.

The generic extractor removed hidden variants and globally deduplicated text. Visibility changes therefore changed which rate's amount survived. Replaying two independently fetched DOMs for KTM event 937 produced different old bodies (6,952 / 7,084 characters). The new extraction produced the same 4,530-character body from both. Actual rate prices and summary amounts remain monitored.

Hello's body included previous/next event navigation, site footer and unrelated popup controls. The October 29 comparison for event 586 changed from a neighbouring event title to `다음글이 없습니다.`. Scope the campaign to `.boardView .substance`; notices come only from official `.noticeWrapNew` / `.viewNotice` within it. Keep actual notice changes, including payment dates.

## Changes

- New `campaign_content.py` reads KTM `.c-board__content` and Hello `.boardView .substance` independently of visibility, retaining inline phrases and repeated contractual values.
- Exclude the named KTM site-wide modules PRMT0001/0015/0019/0017/0023 from the body. Preserve PRMT0007 official notices in the separate notice extraction. Do not blacklist every PRMT module.
- Keep each rate-benefit variant, including hidden variants. Do not delete monetary terms with regex noise rules.
- Propagate body extraction version / status. First corrected body establishes a new baseline instead of claiming a promotion change. A notice scope correction remains reviewable, including after an unconfirmed attempt.
- Keep internal fallback provenance in JSON, but remove per-event “이전 수집값 보존” output. Return one collection-status summary per company; empty observations are collection failures, not policy deletion candidates. Failed collections do not claim “특이사항 없음”.
- Increment shared monitor version so a running dashboard reloads the revised warning logic.

## Validation

- 108 local unit/regression tests passed. Tests cover visibility changes, actual rate/benefit/payment-date edits, notice scope migration and grouped collection warnings.
- Four current browser pages (KTM 937/954, Hello 586/8571) fetched twice independently: revised body and notice text identical in each pair.
- All 12 current Hello URLs opened in a browser: 11 official textual notices; the targeted guest page has no sufficient notice text and remains unconfirmed. All 12 campaign roots found.
- 40 saved KTM rendered DOMs plus the 12 current Hello DOMs replayed with hidden/expanded CSS/ARIA state changed: all 52 bodies invariant. This is DOM replay, not 52 twice-fetched live pages.
- Four KTM pages previously lacking structured notice text still remain unconfirmed. No OCR was added. No manual production crawl or Slack message was sent during validation.

Historical raw snapshots are retained as evidence. Old KTM bodies that already lost rate associations cannot be reconstructed safely from flattened text. Revised extraction applies to future runs; no blanket suppression of actual amount/date changes is used.
