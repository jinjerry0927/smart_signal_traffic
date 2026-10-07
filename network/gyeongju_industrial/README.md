# 경주 산업로 3교차로 SUMO 네트워크

용강네거리(`gj_yonggang`), 근화여고네거리(`gj_geunhwa`),
승삼네거리(`gj_seungsam`)를 잇는 지도 기반 실험 네트워크다. OSM의
분리차도와 기존 연결로 기하를 유지하고, 세 신호와 여덟 외부 경계를
`contract.json`에 고정했다. 현행 도로를 정밀 측량해 재현한 자료는 아니다.

## 재현

차량이 포함된 기본 시나리오를 바로 열려면:

```powershell
sumo-gui -c network/gyeongju_industrial/demo.sumocfg
```

GUI의 실행 버튼을 누른다. `demo.rou.xml`은 seed 42의 평시 600초 유입
시나리오이며 차량 382대가 포함된다. 마지막 차량 도착까지 실행한다.
파란색 승용차·주황색 화물차·노란색 버스를 사용한다.

평시·북행 편중·남행 편중의 전체 검증 및 별도 GUI 설정 생성:

```powershell
python tools/run_gyeongju_industrial.py --out .codex-artifacts/industrial-check
python tests/test_gyeongju_industrial.py
```

출력 폴더는 새 이름이어야 한다. `--generate-only`는 입력 파일만 만들며
검증 PASS로 표시하지 않는다. 각 수요 폴더의 `view.sumocfg`로 다시 볼 수 있다.

저장된 subset으로 재생성한다.

```powershell
python tools/build_gyeongju_industrial.py --overwrite
```

핀 고정한 원본 OSM 응답에서 subset까지 다시 생성하려면 다음을 실행한다.
빌더는 원본 SHA-256을 먼저 검사한다.

```powershell
python tools/build_gyeongju_industrial.py `
  --source .codex-artifacts/industrial-20260925/industrial-expanded.osm `
  --overwrite
```

다른 폴더에 생성할 때는 `--output-dir`을 쓴다. 생성 파일이 이미 있으면
기본적으로 중단하며, 명시적인 `--overwrite`에서만 덮어쓴다. 계약의 파일
경로는 번들 기준 상대경로이므로 출력 폴더가 달라도 같은 내용으로 생성된다.

30초 무수요 smoke test:

```powershell
sumo -c network/gyeongju_industrial/industrial.sumocfg
```

수요·검증 코드는 `contract.json`의 외부 경계 edge, 신호 단계, 제어 회전을
사용한다. 모든 서로 다른 경계 쌍 56개가 연결되어야 한다. 승용차, 화물차,
버스를 허용하지만 교통량과 차량 비율은 이 네트워크가 아니라 별도 가상 수요가
정한다.

## 모델 가정

- OSM에 차로 수가 없는 분리 간선은 인접 `lanes=2` 구간을 따라 방향별
  2차로로 가정했다. 차로 수가 없는 양방향 국지도로는 방향별 1차로,
  일방 연결로는 1차로로 가정했다. way별 구분은 계약에 기록했다.
- 제한속도 태그가 없는 간선은 인접 태그를 근거로 70 km/h, 국지도로와
  연결로는 50 km/h로 가정했다. 현장 측정값이나 법정 제한속도 주장이 아니다.
- 2024·2026년 경주시 공식 사진은 용강~청강사 인근 3→4차로 확장 흔적을
  보여 주지만, 공개 자료만으로 촬영 방향이나 특정 OSM way를 확정할 수 없다.
  해당 사업을 근거로 한 특정 way 차로 override는 넣지 않았다.
- netconvert의 `--tls.layout incoming`으로 진입 도로별 보호 신호와 내부
  우선권을 함께 생성한다. 짧은 교차로 내부에서 12 m 차량이 반대 흐름을
  기다리다 경로를 침범하던 문제를 피한다. 같은 진입 도로에서도 충돌하는
  회전은 차로별로 분리하되, 같은 차로의 직진·회전은 함께 열어 앞차가
  뒤차의 통행을 막는 문제를 방지한다. 용강 5개·근화 4개·승삼 4개 녹색
  그룹이며 모두 녹색 30초·황색 3초·전체 적색 2초다.
- 주기는 용강 175초, 근화·승삼 140초다. 재생성 시 netconvert가 긴 주기
  안내 경고를 출력한다. 실험용 보수적 신호이며 현장 신호계획이나 최적
  고정신호가 아니다. AI 성능 비교 전 고정신호 시간·연동 기준선을 별도 설계한다.
- 가상 차량 최대속도는 종류별 모두 약 50 km/h다. 이는 도로 제한속도
  설정과 구별한다. 차량 비율은 승용차/화물차/버스 80/12/8 가중 추출이며
  실제 생성 개수는 각 실행의 `schedule.json`에 기록한다.
- 중간 골목은 유입·유출 0으로 생략했다. 보행, 주차, 버스 정류장과 U-turn은
  모델에서 제외했다.
- 승삼 회전 경로를 확보하기 위해 기본 교차로 반경을 12 m로 설정했으며,
  다른 신호 교차로·일부 합류점에도 적용된다. 현장 측량값은 아니다.

원본과 라이선스 정보는 [source/SOURCE.md](source/SOURCE.md)에 있다.
