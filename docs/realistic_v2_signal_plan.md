# 경주 교차로별 신호계획 상세표

2026-11-03. 생성 근거: Legacy `intersection.json`, V2 `intersection.json`의 실제 연결과 상충표. SUMO 운행 미실행.

방향 N/E/S/W는 저장소 접근로 ID이며 실제 방위각이 아니다. 용강 산업로 접속은 N/E, 기하 직진축은 N/S다.
그룹 순서는 Fixed/Webster의 기본 순서다. AI/Max-Pressure와 안전 마스크는 다른 안전 그룹을 선택할 수 있다.
모든 전환 조건: 그룹 최소녹색 15초, 이동 연속 최대녹색 60초, 종료 이동 황색 3초+소거 2초, 장기 미서비스 우선.
`M번호`는 각 교차로별 V2 TLS link index다. 상충 목록은 양방향 foes와 같은 진출 차로 합류를 합친 보호 이동 쌍이다.
공통 녹색은 전환 중 유지한다. 신규 이동은 소거 종료 전 점등하지 않는다. 조건부 적색 우회전은 없다.

## 용강 (gj_yonggang)

### 원본 접근로·차로 분석

| 진입 방향 | 진입 도로 | 차로 수 | Legacy 차로별 이동 | V2 변경/구조 제약 |
|---|---|---:|---|---|
| N | `yonggang_geunhwa_in` | 2 | yonggang_geunhwa_in_0: 우회전/직진; yonggang_geunhwa_in_1: 직진/좌회전 | 안쪽 1차로 직진 제거, 좌회전 전용; 0차로 직진/우회전 유지 |
| E | `yonggang_southeast_in` | 2 | yonggang_southeast_in_0: 우회전/직진; yonggang_southeast_in_1: 좌회전 | 공유차로는 같은 신호; 방향별 보호 그룹 유지 |
| S | `yonggang_southwest_in` | 2 | yonggang_southwest_in_0: 우회전/직진; yonggang_southwest_in_1: 직진/좌회전 | 안쪽 1차로 직진 제거, 좌회전 전용; 0차로 직진/우회전 유지 |
| W | `yonggang_west_in` | 1 | yonggang_west_in_0: 우회전/직진/좌회전 | 공유차로는 같은 신호; 방향별 보호 그룹 유지 |

### 그룹·현시 순서

| 교차로 | 신호 그룹 ID | 허용 이동 | 진입·진출 차로 | 상충 이동 | 선행·후행 관계 | 전환 조건 | 비고 |
|---|---|---|---|---|---|---|---|
| 용강 | `N_combined` | M0 우회전, M1 직진, M2 좌회전 | M0: `yonggang_geunhwa_in_0` → `yonggang_west_out_0`<br>M1: `yonggang_geunhwa_in_0` → `yonggang_southwest_out_0`<br>M2: `yonggang_geunhwa_in_1` → `yonggang_southeast_out_1` | M4, M5, M7, M8, M9, M10, M11 | W_combined → **N_combined** → NS_through | 최소15/연속최대60, 종료3+2, 미서비스 우선 | 다음 전환 유지: M0, M1 |
| 용강 | `NS_through` | M0 우회전, M1 직진, M6 우회전, M7 직진 | M0: `yonggang_geunhwa_in_0` → `yonggang_west_out_0`<br>M1: `yonggang_geunhwa_in_0` → `yonggang_southwest_out_0`<br>M6: `yonggang_southwest_in_0` → `yonggang_southeast_out_0`<br>M7: `yonggang_southwest_in_0` → `yonggang_geunhwa_out_0` | M2, M3, M4, M5, M8, M9, M10, M11 | N_combined → **NS_through** → S_combined | 최소15/연속최대60, 종료3+2, 미서비스 우선 | 다음 전환 유지: M6, M7 |
| 용강 | `S_combined` | M6 우회전, M7 직진, M8 좌회전 | M6: `yonggang_southwest_in_0` → `yonggang_southeast_out_0`<br>M7: `yonggang_southwest_in_0` → `yonggang_geunhwa_out_0`<br>M8: `yonggang_southwest_in_1` → `yonggang_west_out_0` | M0, M1, M2, M3, M4, M5, M10, M11 | NS_through → **S_combined** → E_combined | 최소15/연속최대60, 종료3+2, 미서비스 우선 | 다음 전환 유지: 없음 |
| 용강 | `E_combined` | M3 우회전, M4 직진, M5 좌회전 | M3: `yonggang_southeast_in_0` → `yonggang_geunhwa_out_0`<br>M4: `yonggang_southeast_in_0` → `yonggang_west_out_0`<br>M5: `yonggang_southeast_in_1` → `yonggang_southwest_out_1` | M0, M1, M2, M7, M8, M10, M11 | S_combined → **E_combined** → W_combined | 최소15/연속최대60, 종료3+2, 미서비스 우선 | 다음 전환 유지: 없음 |
| 용강 | `W_combined` | M9 우회전, M10 직진, M11 좌회전 | M9: `yonggang_west_in_0` → `yonggang_southwest_out_0`<br>M10: `yonggang_west_in_0` → `yonggang_southeast_out_0`<br>M11: `yonggang_west_in_0` → `yonggang_geunhwa_out_1` | M1, M2, M4, M5, M6, M7, M8 | E_combined → **W_combined** → N_combined | 최소15/연속최대60, 종료3+2, 미서비스 우선 | 다음 전환 유지: 없음 |

### 이동·차로·상충 전수표

| 이동 ID | 진입 차로 | 진출 차로 | 회전 | 보호 상충 이동 | 서비스 그룹 |
|---|---|---|---|---|---|
| M0 | `yonggang_geunhwa_in_0` | `yonggang_west_out_0` | 우회전 | M4, M8 | N_combined, NS_through |
| M1 | `yonggang_geunhwa_in_0` | `yonggang_southwest_out_0` | 직진 | M4, M8, M9, M10, M11 | N_combined, NS_through |
| M2 | `yonggang_geunhwa_in_1` | `yonggang_southeast_out_1` | 좌회전 | M4, M5, M7, M11 | N_combined |
| M3 | `yonggang_southeast_in_0` | `yonggang_geunhwa_out_0` | 우회전 | M7 | E_combined |
| M4 | `yonggang_southeast_in_0` | `yonggang_west_out_0` | 직진 | M0, M1, M2, M7, M8, M11 | E_combined |
| M5 | `yonggang_southeast_in_1` | `yonggang_southwest_out_1` | 좌회전 | M2, M7, M8, M10 | E_combined |
| M6 | `yonggang_southwest_in_0` | `yonggang_southeast_out_0` | 우회전 | M10 | NS_through, S_combined |
| M7 | `yonggang_southwest_in_0` | `yonggang_geunhwa_out_0` | 직진 | M2, M3, M4, M5, M10 | NS_through, S_combined |
| M8 | `yonggang_southwest_in_1` | `yonggang_west_out_0` | 좌회전 | M0, M1, M4, M5, M10, M11 | S_combined |
| M9 | `yonggang_west_in_0` | `yonggang_southwest_out_0` | 우회전 | M1 | W_combined |
| M10 | `yonggang_west_in_0` | `yonggang_southeast_out_0` | 직진 | M1, M5, M6, M7, M8 | W_combined |
| M11 | `yonggang_west_in_0` | `yonggang_geunhwa_out_1` | 좌회전 | M1, M2, M4, M8 | W_combined |

## 근화여고 (gj_geunhwa)

### 원본 접근로·차로 분석

| 진입 방향 | 진입 도로 | 차로 수 | Legacy 차로별 이동 | V2 변경/구조 제약 |
|---|---|---:|---|---|
| N | `geunhwa_seungsam_in` | 2 | geunhwa_seungsam_in_0: 우회전/직진; geunhwa_seungsam_in_1: 직진/좌회전 | 안쪽 1차로 직진 제거, 좌회전 전용; 0차로 직진/우회전 유지 |
| E | `geunhwa_east_in` | 1 | geunhwa_east_in_0: 우회전/직진/좌회전 | 공유차로는 같은 신호; 방향별 보호 그룹 유지 |
| S | `yonggang_geunhwa_out` | 2 | yonggang_geunhwa_out_0: 우회전/직진; yonggang_geunhwa_out_1: 직진/좌회전 | 안쪽 1차로 직진 제거, 좌회전 전용; 0차로 직진/우회전 유지 |
| W | `geunhwa_west_in` | 1 | geunhwa_west_in_0: 우회전/직진/좌회전 | 공유차로는 같은 신호; 방향별 보호 그룹 유지 |

### 그룹·현시 순서

| 교차로 | 신호 그룹 ID | 허용 이동 | 진입·진출 차로 | 상충 이동 | 선행·후행 관계 | 전환 조건 | 비고 |
|---|---|---|---|---|---|---|---|
| 근화여고 | `NS_left` | M2 좌회전, M8 좌회전 | M2: `geunhwa_seungsam_in_1` → `geunhwa_east_out_0`<br>M8: `yonggang_geunhwa_out_1` → `geunhwa_west_out_0` | M0, M1, M4, M5, M6, M7, M10, M11 | W_combined → **NS_left** → NS_through | 최소15/연속최대60, 종료3+2, 미서비스 우선 | 다음 전환 유지: 없음 |
| 근화여고 | `NS_through` | M0 우회전, M1 직진, M6 우회전, M7 직진 | M0: `geunhwa_seungsam_in_0` → `geunhwa_west_out_0`<br>M1: `geunhwa_seungsam_in_0` → `yonggang_geunhwa_in_0`<br>M6: `yonggang_geunhwa_out_0` → `geunhwa_east_out_0`<br>M7: `yonggang_geunhwa_out_0` → `geunhwa_seungsam_out_0` | M2, M3, M4, M5, M8, M9, M10, M11 | NS_left → **NS_through** → E_combined | 최소15/연속최대60, 종료3+2, 미서비스 우선 | 다음 전환 유지: 없음 |
| 근화여고 | `E_combined` | M3 우회전, M4 직진, M5 좌회전 | M3: `geunhwa_east_in_0` → `geunhwa_seungsam_out_0`<br>M4: `geunhwa_east_in_0` → `geunhwa_west_out_0`<br>M5: `geunhwa_east_in_0` → `yonggang_geunhwa_in_1` | M0, M1, M2, M7, M8, M10, M11 | NS_through → **E_combined** → W_combined | 최소15/연속최대60, 종료3+2, 미서비스 우선 | 다음 전환 유지: 없음 |
| 근화여고 | `W_combined` | M9 우회전, M10 직진, M11 좌회전 | M9: `geunhwa_west_in_0` → `yonggang_geunhwa_in_0`<br>M10: `geunhwa_west_in_0` → `geunhwa_east_out_0`<br>M11: `geunhwa_west_in_0` → `geunhwa_seungsam_out_1` | M1, M2, M4, M5, M6, M7, M8 | E_combined → **W_combined** → NS_left | 최소15/연속최대60, 종료3+2, 미서비스 우선 | 다음 전환 유지: 없음 |

### 이동·차로·상충 전수표

| 이동 ID | 진입 차로 | 진출 차로 | 회전 | 보호 상충 이동 | 서비스 그룹 |
|---|---|---|---|---|---|
| M0 | `geunhwa_seungsam_in_0` | `geunhwa_west_out_0` | 우회전 | M4, M8 | NS_through |
| M1 | `geunhwa_seungsam_in_0` | `yonggang_geunhwa_in_0` | 직진 | M4, M8, M9, M10, M11 | NS_through |
| M2 | `geunhwa_seungsam_in_1` | `geunhwa_east_out_0` | 좌회전 | M4, M5, M6, M7, M10, M11 | NS_left |
| M3 | `geunhwa_east_in_0` | `geunhwa_seungsam_out_0` | 우회전 | M7 | E_combined |
| M4 | `geunhwa_east_in_0` | `geunhwa_west_out_0` | 직진 | M0, M1, M2, M7, M8, M11 | E_combined |
| M5 | `geunhwa_east_in_0` | `yonggang_geunhwa_in_1` | 좌회전 | M2, M7, M8, M10 | E_combined |
| M6 | `yonggang_geunhwa_out_0` | `geunhwa_east_out_0` | 우회전 | M2, M10 | NS_through |
| M7 | `yonggang_geunhwa_out_0` | `geunhwa_seungsam_out_0` | 직진 | M2, M3, M4, M5, M10 | NS_through |
| M8 | `yonggang_geunhwa_out_1` | `geunhwa_west_out_0` | 좌회전 | M0, M1, M4, M5, M10, M11 | NS_left |
| M9 | `geunhwa_west_in_0` | `yonggang_geunhwa_in_0` | 우회전 | M1 | W_combined |
| M10 | `geunhwa_west_in_0` | `geunhwa_east_out_0` | 직진 | M1, M2, M5, M6, M7, M8 | W_combined |
| M11 | `geunhwa_west_in_0` | `geunhwa_seungsam_out_1` | 좌회전 | M1, M2, M4, M8 | W_combined |

## 승삼 (gj_seungsam)

### 원본 접근로·차로 분석

| 진입 방향 | 진입 도로 | 차로 수 | Legacy 차로별 이동 | V2 변경/구조 제약 |
|---|---|---:|---|---|
| N | `seungsam_north_in` | 2 | seungsam_north_in_0: 우회전/직진; seungsam_north_in_1: 직진/좌회전 | 안쪽 1차로 직진 제거, 좌회전 전용; 0차로 직진/우회전 유지 |
| E | `seungsam_east_in` | 1 | seungsam_east_in_0: 우회전/직진/좌회전 | 공유차로는 같은 신호; 방향별 보호 그룹 유지 |
| S | `geunhwa_seungsam_out` | 2 | geunhwa_seungsam_out_0: 우회전/직진; geunhwa_seungsam_out_1: 직진/좌회전 | 안쪽 1차로 직진 제거, 좌회전 전용; 0차로 직진/우회전 유지 |
| W | `seungsam_west_in` | 1 | seungsam_west_in_0: 우회전/직진/좌회전 | 공유차로는 같은 신호; 방향별 보호 그룹 유지 |

### 그룹·현시 순서

| 교차로 | 신호 그룹 ID | 허용 이동 | 진입·진출 차로 | 상충 이동 | 선행·후행 관계 | 전환 조건 | 비고 |
|---|---|---|---|---|---|---|---|
| 승삼 | `S_combined` | M6 우회전, M7 직진, M8 좌회전 | M6: `geunhwa_seungsam_out_0` → `seungsam_east_out_0`<br>M7: `geunhwa_seungsam_out_0` → `seungsam_north_out_0`<br>M8: `geunhwa_seungsam_out_1` → `seungsam_west_out_0` | M0, M1, M2, M3, M4, M5, M10, M11 | E_combined → **S_combined** → NS_through | 최소15/연속최대60, 종료3+2, 미서비스 우선 | 다음 전환 유지: M6, M7 |
| 승삼 | `NS_through` | M0 우회전, M1 직진, M6 우회전, M7 직진 | M0: `seungsam_north_in_0` → `seungsam_west_out_0`<br>M1: `seungsam_north_in_0` → `geunhwa_seungsam_in_0`<br>M6: `geunhwa_seungsam_out_0` → `seungsam_east_out_0`<br>M7: `geunhwa_seungsam_out_0` → `seungsam_north_out_0` | M2, M3, M4, M5, M8, M9, M10, M11 | S_combined → **NS_through** → N_combined | 최소15/연속최대60, 종료3+2, 미서비스 우선 | 다음 전환 유지: M0, M1 |
| 승삼 | `N_combined` | M0 우회전, M1 직진, M2 좌회전 | M0: `seungsam_north_in_0` → `seungsam_west_out_0`<br>M1: `seungsam_north_in_0` → `geunhwa_seungsam_in_0`<br>M2: `seungsam_north_in_1` → `seungsam_east_out_0` | M4, M5, M6, M7, M8, M9, M10, M11 | NS_through → **N_combined** → W_combined | 최소15/연속최대60, 종료3+2, 미서비스 우선 | 다음 전환 유지: 없음 |
| 승삼 | `W_combined` | M9 우회전, M10 직진, M11 좌회전 | M9: `seungsam_west_in_0` → `geunhwa_seungsam_in_0`<br>M10: `seungsam_west_in_0` → `seungsam_east_out_0`<br>M11: `seungsam_west_in_0` → `seungsam_north_out_1` | M1, M2, M4, M5, M6, M7, M8 | N_combined → **W_combined** → E_combined | 최소15/연속최대60, 종료3+2, 미서비스 우선 | 다음 전환 유지: 없음 |
| 승삼 | `E_combined` | M3 우회전, M4 직진, M5 좌회전 | M3: `seungsam_east_in_0` → `seungsam_north_out_0`<br>M4: `seungsam_east_in_0` → `seungsam_west_out_0`<br>M5: `seungsam_east_in_0` → `geunhwa_seungsam_in_1` | M0, M1, M2, M7, M8, M10, M11 | W_combined → **E_combined** → S_combined | 최소15/연속최대60, 종료3+2, 미서비스 우선 | 다음 전환 유지: 없음 |

### 이동·차로·상충 전수표

| 이동 ID | 진입 차로 | 진출 차로 | 회전 | 보호 상충 이동 | 서비스 그룹 |
|---|---|---|---|---|---|
| M0 | `seungsam_north_in_0` | `seungsam_west_out_0` | 우회전 | M4, M8 | NS_through, N_combined |
| M1 | `seungsam_north_in_0` | `geunhwa_seungsam_in_0` | 직진 | M4, M8, M9, M10, M11 | NS_through, N_combined |
| M2 | `seungsam_north_in_1` | `seungsam_east_out_0` | 좌회전 | M4, M5, M6, M7, M10, M11 | N_combined |
| M3 | `seungsam_east_in_0` | `seungsam_north_out_0` | 우회전 | M7 | E_combined |
| M4 | `seungsam_east_in_0` | `seungsam_west_out_0` | 직진 | M0, M1, M2, M7, M8, M11 | E_combined |
| M5 | `seungsam_east_in_0` | `geunhwa_seungsam_in_1` | 좌회전 | M2, M7, M8, M10 | E_combined |
| M6 | `geunhwa_seungsam_out_0` | `seungsam_east_out_0` | 우회전 | M2, M10 | S_combined, NS_through |
| M7 | `geunhwa_seungsam_out_0` | `seungsam_north_out_0` | 직진 | M2, M3, M4, M5, M10 | S_combined, NS_through |
| M8 | `geunhwa_seungsam_out_1` | `seungsam_west_out_0` | 좌회전 | M0, M1, M4, M5, M10, M11 | S_combined |
| M9 | `seungsam_west_in_0` | `geunhwa_seungsam_in_0` | 우회전 | M1 | W_combined |
| M10 | `seungsam_west_in_0` | `seungsam_east_out_0` | 직진 | M1, M2, M5, M6, M7, M8 | W_combined |
| M11 | `seungsam_west_in_0` | `seungsam_north_out_1` | 좌회전 | M1, M2, M4, M8 | W_combined |
