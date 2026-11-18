# 프로모션 Gmail 알림

일간 프로모션 워크플로는 수집·비교 후 메일 본문을 runner 임시 폴더에 작성한다.
기존 데이터 저장과 대시보드 배포를 수행한 뒤 Gmail SMTP(465/TLS)로 전송한다.
슬랙 웹훅은 일간 워크플로에서 사용하지 않는다.

- 수신: jiwonchoi@sk.com
- 발신: `MAIL_USERNAME`의 Gmail 계정 (표시 이름 Promotion Monitor)
- 프로모션 변경, 유의사항 변경, 수집 점검이 있을 때 발송한다.
- 변경·점검이 모두 없으면 메일을 생략한다.
- 회사별 변경 건수, 유의사항 요약과 원문 링크, 대시보드 링크를 포함한다.
- 일부 회사 수집 실패는 정상 결과와 함께 점검 안내로 전달한다.
- 크롤러/분석 단계 자체가 실패하면 실행 로그 링크를 별도 메일로 보낸다.
- 발송 실패는 Actions에 실패로 표시되며 이미 저장·배포한 데이터를 되돌리지 않는다.

## 기존 커뮤니티 모니터링 Gmail 설정 재사용

`mvno-market-monitor/.github/workflows/market_monitor.yml`과 같은
`dawidd6/action-send-mail@v3` 및 인증 변수 이름을 사용한다.

`competitor-monitor` 저장소의 Settings → Secrets and variables → Actions에
다음 저장소 secret을 등록하거나, 해당 저장소에서 사용할 수 있는 조직 secret으로 제공한다.

- `MAIL_USERNAME`: 기존 발신 Gmail 주소
- `MAIL_APP_PASSWORD`: 해당 계정의 Gmail 앱 비밀번호

다른 저장소에 등록한 repository secret은 자동 공유되지 않는다.
GitHub는 기존 비밀번호 값을 다시 보여주지 않으므로 보관한 값을 재등록하거나
같은 Gmail 계정에서 별도 앱 비밀번호를 발급해 이 저장소에 등록한다.
비밀번호를 코드, PR, 채팅에 적지 않는다.

검증은 `python -m unittest discover -s tests -v`로 수행하며 외부 메일을 발송하지 않는다.
설정 후 일간 워크플로의 다음 변경 알림으로 회사 메일 수신을 확인한다.
수동 실행은 실제 수집·저장·배포와 조건에 따른 메일 발송을 모두 수행한다.
