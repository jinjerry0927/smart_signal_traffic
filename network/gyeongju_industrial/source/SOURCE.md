# OSM 출처

- URL: https://api.openstreetmap.org/api/0.6/map?bbox=129.215,35.862,129.234,35.882
- 취득일: 2026-09-25
- 원본 SHA-256: `f2dfeae3893d41a6fcf70652ef045e1cb1dfb4294435acaa5b06af61cf966f27`
- subset SHA-256: `81e39e23a5dc63dccf9a745c4cb0f6902f6b83ac8d2205cd1b78525692dfa38f`
- 저작자: OpenStreetMap contributors
- 라이선스: Open Database License 1.0 (ODbL)
- 라이선스 안내: https://www.openstreetmap.org/copyright

`industrial_corridor.osm.xml`은 위 API 응답에서 세 교차로와 연결되는 OSM
way 29개를 신호 anchor에서 최대 약 330 m 범위로 자른 재현용 subset이다.
경계에서 원본 선분을 보간한 음수 ID node를 추가했고, 그 외 OSM 도로 기하와
태그를 보존했다. 신호 node 태그, 누락 차로 수와 누락 제한속도는 빌더가
시뮬레이션 입력으로 명시하며 세부 way 목록과 분류는 `contract.json`에 있다.

도로 확장 참고 자료:

- 경주시, 2024-12-26: https://www.gyeongju.go.kr/news/page.do?mnu_uid=1336&parm_bod_uid=287281&step=258
- 경주시, 2026-04-09: https://www.gyeongju.go.kr/news/page.do?mnu_uid=1336&parm_bod_uid=317706&step=258

위 공식 자료의 사진과 설명만으로 촬영 방향·구간을 특정 OSM way에 대응시킬
수 없어, 차로 확장을 way별 현행 차로 수 근거로 사용하지 않았다.
