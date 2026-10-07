# OSM 출처

- URL: https://api.openstreetmap.org/api/0.6/map?bbox=129.199,35.849,129.213,35.861
- 취득일: 2026-09-17
- SHA-256: `91fb1a319ebaedae234fa5b4ff3d1ee904e5011ab0e44f38775df0d5e55c559b`
- 저작자: OpenStreetMap contributors
- 라이선스: Open Database License 1.0 (ODbL)
- 라이선스 안내: https://www.openstreetmap.org/copyright

`gyeongju_corridor.osm.xml`은 위 응답에서 세 교차로의 주도로를 이루는 OSM
way 12개와 필요한 node만 보존한 재현용 subset이다. 원본 응답의 도로 기하,
분리차도, `oneway`, `lanes` 태그를 유지하며, `maxspeed`가 없는 대상 way에는
시뮬레이션 가정 `50` km/h를 추가했다. 각 외부 팔은 가장 가까운 신호에서
약 229~287 m 떨어진 OSM node에서 끝난다.
