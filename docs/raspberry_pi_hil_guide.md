# 라즈베리파이 HIL 실습 런북 (Raspberry Pi HIL Runbook)

> 다음에 Pi를 다시 켜고 실습할 때 **이 문서만 보고 빠르게 재현**하기 위한 매뉴얼.
> 2026-06-07 첫 HIL 데모 성공 기준으로 정리. 막혔던 함정까지 포함.

---

## 0. 이게 뭐였나 (한 줄 구조)

```
노트북(SUMO + demo.py)  ──WiFi 소켓(JSON)──▶  라즈베리파이(edge_server.py, numpy 추론)
        └──────────────◀── action(다음 신호) ──────────────┘
```

- 카메라 대신 **E2 검지기 관측(53D)** 으로 학습한 모델(`smart_signal_e2.npz`)을
- **Pi에서 torch 없이 numpy로 추론**하고, 그 결과로 SUMO 신호를 실시간 제어한다(HIL).

---

## 1. 내 설정값 (외워둘 것)

| 항목 | 값 |
|---|---|
| Pi 모델 | Raspberry Pi 5 |
| 사용자명 | `jinjerry` |
| 호스트네임 | `raspberrypi` |
| 접속 방식 | 헤드리스(모니터 없이 노트북에서 SSH) |
| 망 | 폰 핫스팟 (노트북·Pi 둘 다 같은 핫스팟에 연결) |
| Pi IP | **매번 바뀜** → 핫스팟 "연결된 기기"에서 확인 (예: `10.14.223.27`) |
| Pi에 둔 파일 | `~/smart_signal_e2.npz`, `~/edge_server.py` |
| 엣지서버 포트 | `9999` |

> ⚠️ **핫스팟을 껐다 켜면 Pi IP가 바뀐다.** 항상 현재 IP를 다시 확인할 것.

---

## 2. 빠른 시작 (TL;DR — 이미 셋업 끝난 경우)

**창 A = Pi(SSH) / 창 B = 노트북 PowerShell**, 두 창을 쓴다.

```text
① 폰 핫스팟 켜기 → 노트북도 그 핫스팟에 연결 → Pi 전원 → 1~2분 대기
② 폰 "연결된 기기"에서 raspberrypi의 현재 IP 확인  (예: 10.14.223.27)
③ [창 A] ssh jinjerry@<IP>
④ [창 A] sudo iw wlan0 set power_save off          # 끊김 방지
⑤ [창 A] pkill -f edge_server.py; nohup python3 -u edge_server.py --weights smart_signal_e2.npz > edge.log 2>&1 &
⑥ [창 A] ss -tln | grep 9999                        # 0.0.0.0:9999 보이면 서버 OK
⑦ [창 B] cd <프로젝트폴더>; python demo.py --hil --host <IP> --scenario asymmetric --duration 600
```

`[hil] 엣지서버 연결: <IP>:9999` 뜨고 SUMO GUI에서 신호 제어되면 성공. 🎉

---

## 2-A. 대시보드까지 같이 띄우기 (발표용 — 0~4단계 풀버전)

> Pi로 시연하면서 노트북에 **실시간 대시보드**(E2 차로별 대기차량 + 두뇌 KEEP/SWITCH + Q값)도 같이 띄우는 전체 절차. **창 2개**를 열고 번갈아 작업한다. `<IP>`는 매번 바뀌니 그 자리에 실제 Pi IP를 넣는다.

### 0. 준비 (창 열기 전)
1. 폰 **핫스팟 켜기** → 데모 끝까지 끄지 말 것(끄면 IP 바뀜).
2. **노트북**을 그 핫스팟에 연결(학교/집 WiFi 아님).
3. Pi에 **USB-C 전원** 연결 → **1~2분** 부팅 대기.
4. 폰 **"연결된 기기"** 목록에서 `raspberrypi` 의 IP 확인(예: `10.14.223.27`). → 이게 `<IP>`.

### 1. 창 B (노트북) — 폴더 진입 + Pi에 최신 서버 복사
PowerShell 새 창:
```powershell
# 이 저장소를 내려받은 폴더에서 PowerShell을 연 뒤 실행
git pull
scp edge_server.py jinjerry@<IP>:~/
```
- `git pull` → 최신 코드(대시보드/Q값 반영분) 확보.
- `scp ...` → **Q값 막대 때문에** 새 edge_server.py를 Pi로 보냄(한 번만 하면 됨). 비밀번호는 화면에 안 보이는 게 정상.

### 2. 창 A (Pi 접속) — SSH 후 엣지서버 기동
PowerShell **다른** 새 창:
```powershell
ssh jinjerry@<IP>
```
`jinjerry@raspberrypi:~ $` 프롬프트가 뜨면 접속 성공. 이제 **Pi 안에서**:
```bash
sudo iw wlan0 set power_save off
pkill -f edge_server.py
nohup python3 -u edge_server.py --weights smart_signal_e2.npz > edge.log 2>&1 &
ss -tln | grep 9999
```
- 마지막 줄에 `LISTEN ... 0.0.0.0:9999` 보이면 서버 OK. (확인: `cat edge.log` → `[edge] listening on 0.0.0.0:9999`)

### 3. 창 B (노트북) — 대시보드 포함 데모 실행
창 B로 돌아와서(이미 폴더 안):
```powershell
python demo.py --hil --dashboard --host <IP> --scenario asymmetric --duration 600
```
- ⚠️ 옵션은 전부 `--단어` 처럼 **붙여서**: `--hil`/`--dashboard`/`--host`/`--scenario`/`--duration` (`-- hil` 처럼 띄우면 에러).
- ⚠️ `--host` 엔 **IP만**(`jinjerry@` 금지, `<>` 금지, `.local` 말고 숫자 IP).
- `[hil] 엣지서버 연결: <IP>:9999` + `[dashboard] http://127.0.0.1:8000` 뜨면서 **SUMO GUI + 브라우저 대시보드**가 자동으로 열린다.

### 4. 시연
- SUMO 창에서 **▶ 재생**(또는 상단 Delay 50~100ms).
- 브라우저 대시보드에서 12차로 막대·KEEP/SWITCH·**Q값 막대**가 실시간 갱신 → 하단 범례에 `엣지: Pi <IP> …` 표시(= Pi가 추론 중인 증거).
- 시나리오 바꾸기: `--scenario low|medium|high|asymmetric|saturated`

> 대시보드 없이 콘솔로만 볼 거면 `--dashboard` 만 빼면 된다(2번 빠른 시작과 동일).

---

## 2-B. 안전 종료 (전원 뽑기 전 필수)

전원을 그냥 뽑으면 SD카드가 손상될 수 있다. **반드시 graceful shutdown 후** 뽑는다.

```bash
# 창 A(SSH)에서
pkill -f edge_server.py     # (선택) 엣지서버 정리
sudo shutdown -h now        # 안전 종료 (= sudo poweroff)
```
- 실행하면 SSH가 끊긴다 → `Connection closed by remote host` 는 **정상**.
- Pi의 **초록 활동 LED 깜빡임이 멈출 때까지**(보통 5~15초) 기다린 뒤 **USB-C 전원 뽑기** → 마지막에 핫스팟 끄기.
- ❌ 활동 LED 깜빡이는 중에 전원 뽑기 = SD카드 손상 위험.

---

## 3. 상세 단계

### A. 핫스팟 + 부팅
1. 폰 **핫스팟 켜기** (데모 끝날 때까지 끄지 말 것 — 끄면 IP 바뀜)
2. **노트북**을 그 핫스팟에 연결 (학교/집 WiFi 아님 — Pi랑 같은 망이어야 함)
3. Pi에 **USB-C 전원** 연결 → **1~2분** 부팅 + 핫스팟 자동 재접속 대기

### B. Pi 현재 IP 찾기 (★ 매번)
- 폰 핫스팟 설정 → **연결된 기기 목록** → `raspberrypi` 의 IP 확인
- 이 IP를 창 A(ssh)와 창 B(demo --host) **양쪽에 동일하게** 사용

### C. SSH 접속 (창 A)
```powershell
ssh jinjerry@<IP>
```
- 비밀번호 입력 (화면에 안 보이는 게 정상)
- `jinjerry@raspberrypi:~ $` 뜨면 성공
- ⚠️ `raspberrypi.local` 은 윈도우에서 엉뚱한 옛 주소로 잡혀 timeout 날 수 있음 → **숫자 IP 사용**

### D. (최초 1회만) 파일 준비
- **numpy 확인** (창 A):
  ```bash
  python3 -c "import numpy; print('numpy OK', numpy.__version__)"
  # 없으면: sudo apt update && sudo apt install -y python3-numpy
  ```
- **파일 복사** (창 B, 노트북 프로젝트 폴더에서):
  ```powershell
  git pull   # 최신 모델/스크립트 확보
  scp results/smart_signal_e2.npz edge_server.py jinjerry@<IP>:~/
  ```
  > Pi엔 **numpy만** 필요 (torch 불필요). 모델은 `.npz`(numpy 가중치)라 가벼움.

### E. 엣지서버 실행 (창 A)
```bash
sudo iw wlan0 set power_save off                                   # 끊김 방지(권장)
pkill -f edge_server.py                                            # 기존 서버 정리
nohup python3 -u edge_server.py --weights smart_signal_e2.npz > edge.log 2>&1 &
ss -tln | grep 9999                                               # 0.0.0.0:9999 보이면 OK
```
- `nohup ... &` → SSH 끊겨도 서버 유지 / `-u` → 로그 즉시 기록
- 로그 보기: `cat edge.log` → `[edge] listening on 0.0.0.0:9999`
- 서버 끄기: `pkill -f edge_server.py`

### F. 데모 실행 (창 B, 노트북)
```powershell
python demo.py --hil --host <IP> --scenario asymmetric --duration 600
```
- ⚠️ `--host` 엔 **IP만** (절대 `jinjerry@` 붙이지 말 것 — 그건 SSH 전용)
- `[hil] 엣지서버 연결: <IP>:9999` + SUMO GUI 제어되면 성공
- 시나리오 바꾸기: `--scenario low|medium|high|asymmetric|saturated`
- Pi가 추론한 증거 확인: 창 A에서 `cat edge.log` → `connected (...)` 로그

---

## 4. 자주 막히는 곳 (오늘 실제로 겪은 것)

| 증상 | 원인 | 해결 |
|---|---|---|
| `ssh ... Connection timed out` (재접속) | 핫스팟 재시작으로 **IP 바뀜** | 연결기기 목록에서 **새 IP** 확인 후 그 IP로 접속 |
| `ssh ...` 처음부터 timeout | **SSH 미활성** 상태로 구움 | SD 재굽기 → 서비스 탭 **SSH 사용** 체크 |
| `Could not resolve hostname` | `.local` 이름풀이 실패 | **숫자 IP** 사용 |
| `Connection reset ... port 22` | WiFi 절전/전원 끊김 | `sudo iw wlan0 set power_save off`, 전원 점검(아래) |
| `FileNotFoundError: ...npy` | 파일명 **`.npy` 오타** (실제 `.npz`) | `--weights smart_signal_e2.npz` (Tab 자동완성 추천) |
| `edge.log`에 `nohup: ignoring input`만 | 정상 안내문 + 출력 버퍼 | 에러 아님. `ss -tln \| grep 9999` 로 서버 확인 |
| `getaddrinfo failed` (demo) | `--host`에 `jinjerry@` 붙임 | `--host <IP>` (IP만) |
| `Missing yellow phase` 경고 | 우회전 상시신호 설계상 정상 | **무시** (계획서에도 기록된 정상 경고) |

### 전원 점검 (Pi 5는 전원 까다로움)
```bash
vcgencmd get_throttled
# throttled=0x0  → 정상
# 0x0 아님       → 저전압. 5V/5A(27W) 정품급 USB-C 어댑터로 교체
```

---

## 5. 처음부터 다시 셋업 (SD카드 재굽기)

Raspberry Pi Imager → CHOOSE DEVICE(Pi 5) / OS(Raspberry Pi OS 64-bit) / STORAGE(microSD)
→ **NEXT → 설정 편집(EDIT SETTINGS)**:
- **일반:** 호스트네임 `raspberrypi` / 사용자 `jinjerry`+비번 / 무선LAN **핫스팟 SSID·비번**(오타주의) / 로캘 `Asia/Seoul`
- **서비스:** ☑ **SSH 사용 → 비밀번호 인증** ← 이거 빠뜨리면 timeout
→ SAVE → 굽기 → Pi에 꽂고 부팅 → 3.C 부터 진행

---

## 6. 명령어 치트시트

```bash
# --- Pi 안에서 (창 A) ---
sudo iw wlan0 set power_save off                  # WiFi 끊김 방지
hostname -I                                       # Pi 현재 IP 확인 (Pi 안에서)
ip -4 addr show wlan0                             # WiFi 인터페이스 IP 상세
pkill -f edge_server.py                           # 서버 끄기
nohup python3 -u edge_server.py --weights smart_signal_e2.npz > edge.log 2>&1 &   # 서버 켜기
ss -tln | grep 9999                              # 서버 리스닝 확인
cat edge.log                                      # 서버 로그(연결 기록)
vcgencmd get_throttled                           # 전원 상태(0x0=정상)
```
```powershell
# --- 노트북에서 (창 B) ---
ssh jinjerry@<IP>                                                 # Pi 접속
scp results/smart_signal_e2.npz edge_server.py jinjerry@<IP>:~/   # 파일 복사(최초)
python demo.py --hil --host <IP> --scenario asymmetric --duration 600              # 데모(콘솔만)
python demo.py --hil --dashboard --host <IP> --scenario asymmetric --duration 600  # 데모(+실시간 대시보드)
```

---

## 7. 주의/메모

- **성능 수치는 데모 화면이 아니라 `evaluate.py`(15ep) 결과**를 인용 (asymmetric에서 rl_e2 = Fixed 대비 **+76%**). demo의 단발 대기시간(예 403.4)은 시연용일 뿐 벤치마크 아님.
- 발표 라이브 데모 시: **핫스팟 켠 채 유지**, 미리 IP 확인·서버 기동 후 시작. `edge.log`의 `connected` 캡처를 증거자료로.
- 더 안정적으로 하려면(선택): Pi 고정 IP 설정 or 집 공유기 사용 → IP 재확인 수고 감소.
</content>
</invoke>
