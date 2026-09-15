// 11차 발표 PPT 생성 스크립트
// 디자인 시스템 (9차 계승): navy/teal/accent
//   NAVY=0D1B2A, NAVY2=1a2d40, TEAL=00897B, CYAN=00BCD4, GRAY=546E7A
// 레이아웃: 16:9 (10" x 5.625")
// 내용: (1) 모델 재학습·Webster 추가 이유  (2) 3모델 시연영상  (3) 비교표  (4) 가상 센서

const pptxgen = require("pptxgenjs");

const NAVY   = "0D1B2A";
const NAVY2  = "1a2d40";
const TEAL   = "00897B";
const CYAN   = "00BCD4";
const GRAY   = "546E7A";
const WHITE  = "FFFFFF";
const MUTED  = "90A4AE";
const AMBER  = "FFA726";
const RED    = "FF5252";
const GREEN  = "69F0AE";
const KOR    = "맑은 고딕";
const ENG    = "Calibri";

const pres = new pptxgen();
pres.layout = "LAYOUT_16x9";
pres.title  = "ICT 종합설계 11차 발표";
pres.author = "전형주, 이진욱";

const W = 10.0, H = 5.625;
const TOTAL_SLIDES = 5;

// ───────── 공통 요소 ─────────
function pageHeader(slide, n, sectionTitle, subTitle) {
  slide.addShape(pres.shapes.RECTANGLE, {
    x: 0, y: 0, w: 0.18, h: H, fill: { color: TEAL }, line: { color: TEAL }
  });
  slide.addText(String(n).padStart(2, "0"), {
    x: 0.35, y: 0.2, w: 0.6, h: 0.55,
    fontFace: ENG, fontSize: 28, bold: true, color: CYAN, margin: 0
  });
  slide.addText(sectionTitle, {
    x: 1.0, y: 0.22, w: 7.5, h: 0.45,
    fontFace: KOR, fontSize: 20, bold: true, color: WHITE, margin: 0
  });
  if (subTitle) {
    slide.addText(subTitle, {
      x: 1.0, y: 0.68, w: 8.2, h: 0.3,
      fontFace: KOR, fontSize: 11, color: MUTED, margin: 0
    });
  }
  slide.addText(`${String(n).padStart(2, "0")} / ${String(TOTAL_SLIDES).padStart(2, "0")}`, {
    x: W - 1.3, y: 0.25, w: 1.1, h: 0.3,
    fontFace: ENG, fontSize: 10, color: MUTED, align: "right", margin: 0
  });
  slide.addShape(pres.shapes.RECTANGLE, {
    x: 0.35, y: 1.1, w: W - 0.7, h: 0.015,
    fill: { color: TEAL }, line: { color: TEAL }
  });
}

function footer(slide) {
  slide.addShape(pres.shapes.RECTANGLE, {
    x: 0, y: H - 0.05, w: W, h: 0.05,
    fill: { color: NAVY2 }, line: { color: NAVY2 }
  });
}

// 좌측 액센트 바 달린 카드
function card(slide, x, y, w, h, accent) {
  slide.addShape(pres.shapes.RECTANGLE, {
    x, y, w, h, fill: { color: NAVY2 }, line: { color: accent, width: 1 }
  });
  slide.addShape(pres.shapes.RECTANGLE, {
    x, y, w: 0.08, h, fill: { color: accent }, line: { color: accent }
  });
}

// ───────── 1. 표지 ─────────
{
  const s = pres.addSlide();
  s.background = { color: NAVY };

  s.addShape(pres.shapes.RECTANGLE, {
    x: 0, y: 0, w: 0.4, h: H, fill: { color: TEAL }, line: { color: TEAL }
  });

  s.addText("ICT 종합설계  |  11차 발표", {
    x: 0.7, y: 0.5, w: 7, h: 0.4,
    fontFace: KOR, fontSize: 13, color: CYAN, margin: 0
  });
  s.addText("강화학습 기반", {
    x: 0.7, y: 0.95, w: 7, h: 0.4,
    fontFace: KOR, fontSize: 18, color: MUTED, margin: 0
  });
  s.addText("스마트 교차로 신호 제어 시스템", {
    x: 0.7, y: 1.4, w: 8.8, h: 0.7,
    fontFace: KOR, fontSize: 32, bold: true, color: WHITE, margin: 0
  });
  s.addText("3-모델 시나리오 비교(Fixed · Webster · SmartSignal) + 카메라 한계의 대안 발견", {
    x: 0.7, y: 2.2, w: 8.8, h: 0.4,
    fontFace: KOR, fontSize: 14, color: CYAN, margin: 0
  });
  s.addText("3-Model Scenario Benchmark  &  Virtual Sensing Beyond the Camera", {
    x: 0.7, y: 2.6, w: 8.8, h: 0.3,
    fontFace: ENG, fontSize: 11, italic: true, color: MUTED, margin: 0
  });

  const KP = [
    ["5 × 3",     "시나리오 × 모델 비교"],
    ["Webster",   "공정한 최강 기준선 신설"],
    ["+78%",      "비대칭 대기시간 개선(RL)"],
    ["가상센서",   "카메라 한계 우회"],
  ];
  KP.forEach(([big, lbl], i) => {
    const x = 0.7 + i * 2.15;
    s.addShape(pres.shapes.RECTANGLE, {
      x, y: 3.3, w: 1.95, h: 1.1,
      fill: { color: NAVY2 }, line: { color: TEAL, width: 1 }
    });
    s.addText(big, {
      x: x + 0.15, y: 3.42, w: 1.7, h: 0.5,
      fontFace: KOR, fontSize: 20, bold: true, color: CYAN, margin: 0
    });
    s.addText(lbl, {
      x: x + 0.15, y: 3.92, w: 1.7, h: 0.4,
      fontFace: KOR, fontSize: 10, color: MUTED, margin: 0
    });
  });

  s.addText("과목   ICT 종합설계", {
    x: 0.7, y: 4.7, w: 4, h: 0.3, fontFace: KOR, fontSize: 11, color: MUTED, margin: 0
  });
  s.addText("구성   2인 팀 프로젝트", {
    x: 0.7, y: 5.0, w: 4, h: 0.3, fontFace: KOR, fontSize: 11, color: MUTED, margin: 0
  });
  s.addText("발표자  전형주, 이진욱", {
    x: 5.5, y: 4.85, w: 4, h: 0.3, fontFace: KOR, fontSize: 11, color: MUTED, margin: 0, align: "right"
  });
}

// ───────── 2. 모델 재학습 · Webster 추가 — 왜? ─────────
{
  const s = pres.addSlide();
  s.background = { color: NAVY };
  pageHeader(s, 1, "모델 재학습 · Webster 추가 — 왜?",
             "“끝난 줄 알았던” 학습을 다시 한 이유와 새 기준선의 의미");

  // 카드 A: 왜 다시 학습?
  card(s, 0.35, 1.35, 4.55, 2.15, CYAN);
  s.addText("Q1", {
    x: 0.55, y: 1.45, w: 1.0, h: 0.3,
    fontFace: ENG, fontSize: 13, bold: true, color: CYAN, margin: 0
  });
  s.addText("학습, 끝난 거 아니었나?", {
    x: 1.1, y: 1.45, w: 3.6, h: 0.35,
    fontFace: KOR, fontSize: 15, bold: true, color: WHITE, margin: 0
  });
  s.addText([
    { text: "이전 모델은 과포화 상황에서 발산 (reward −1920 고착) = 학습 실패", options: { bullet: { code: "25A0" }, breakLine: true } },
    { text: "프로젝트를 SUMO 전용으로 재정립 → 모델 정비·개명(SmartSignal)", options: { bullet: { code: "25A0" }, breakLine: true } },
    { text: "처음부터 1000ep 재학습 → 끝난 게 아니라 원인 잡고 다시 세움", options: { bullet: { code: "25A0" } } },
  ], {
    x: 0.6, y: 1.9, w: 4.15, h: 1.5,
    fontFace: KOR, fontSize: 11, color: WHITE, paraSpaceAfter: 6, margin: 0
  });

  // 카드 B: 왜 Webster?
  card(s, 5.1, 1.35, 4.55, 2.15, TEAL);
  s.addText("Q2", {
    x: 5.3, y: 1.45, w: 1.0, h: 0.3,
    fontFace: ENG, fontSize: 13, bold: true, color: CYAN, margin: 0
  });
  s.addText("Fixed vs RL인데, 왜 Webster?", {
    x: 5.85, y: 1.45, w: 3.6, h: 0.35,
    fontFace: KOR, fontSize: 15, bold: true, color: WHITE, margin: 0
  });
  s.addText([
    { text: "Fixed(평범한 고정신호)만 이기는 건 “당연한 승리” → 설득력 약함", options: { bullet: { code: "25A0" }, breakLine: true } },
    { text: "Webster = 그 수요의 이론상 최적 고정주기 = 고정신호의 최선", options: { bullet: { code: "25A0" }, breakLine: true } },
    { text: "실세계 적응신호는 공개 관측 불가 → 가장 공정한 적수로 채택", options: { bullet: { code: "25A0" } } },
  ], {
    x: 5.35, y: 1.9, w: 4.15, h: 1.5,
    fontFace: KOR, fontSize: 11, color: WHITE, paraSpaceAfter: 6, margin: 0
  });

  // 하단: 3단계 비교 사다리
  s.addText("결론  —  3단계 비교 사다리", {
    x: 0.35, y: 3.72, w: 6, h: 0.3,
    fontFace: KOR, fontSize: 13, bold: true, color: CYAN, margin: 0
  });

  const ladder = [
    { name: "Fixed",       sub: "현행 평범 신호",          accent: GRAY },
    { name: "Webster",     sub: "고정신호의 이론적 최선",   accent: TEAL },
    { name: "SmartSignal", sub: "강화학습 정책 (목표)",     accent: CYAN },
  ];
  const lw = 2.7, ly = 4.35, lh = 0.9;
  ladder.forEach((d, i) => {
    const x = 0.35 + i * (lw + 0.6);
    s.addShape(pres.shapes.RECTANGLE, {
      x, y: ly, w: lw, h: lh,
      fill: { color: i === 2 ? TEAL : NAVY2 },
      line: { color: d.accent, width: i === 2 ? 0 : 1 }
    });
    s.addText(d.name, {
      x: x + 0.15, y: ly + 0.13, w: lw - 0.3, h: 0.4,
      fontFace: ENG, fontSize: 17, bold: true,
      color: i === 2 ? NAVY : WHITE, margin: 0
    });
    s.addText(d.sub, {
      x: x + 0.15, y: ly + 0.52, w: lw - 0.3, h: 0.3,
      fontFace: KOR, fontSize: 10,
      color: i === 2 ? NAVY : MUTED, margin: 0
    });
    if (i < 2) {
      s.addText("▶", {
        x: x + lw + 0.06, y: ly + lh / 2 - 0.18, w: 0.48, h: 0.36,
        fontFace: ENG, fontSize: 16, bold: true, color: CYAN,
        align: "center", valign: "middle", margin: 0
      });
    }
  });

  footer(s);
}

// ───────── 3. 3개 모델 시연 영상 ─────────
{
  const s = pres.addSlide();
  s.background = { color: NAVY };
  pageHeader(s, 2, "3개 모델 시연 영상",
             "동일 조건에서 세 정책을 같은 무대 위에 세워 비교");

  // 공정성 배지
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.35, y: 1.28, w: 9.3, h: 0.42,
    fill: { color: NAVY2 }, line: { color: CYAN, width: 1 }
  });
  s.addText([
    { text: "공정성 조건   ", options: { fontSize: 11, bold: true, color: CYAN, fontFace: KOR } },
    { text: "동일 시나리오  ·  동일 수요(seed)  ·  동일 카메라(zoom)  —  차이는 오직 신호제어 정책뿐",
      options: { fontSize: 11, color: WHITE, fontFace: KOR } },
  ], {
    x: 0.55, y: 1.28, w: 9.0, h: 0.42, valign: "middle", margin: 0
  });

  // 3개 영상 프레임
  const vids = [
    { name: "Fixed",       sub: "고정주기 신호",       accent: GRAY },
    { name: "Webster",     sub: "이론상 최적 고정주기",  accent: TEAL },
    { name: "SmartSignal", sub: "강화학습 정책",        accent: CYAN },
  ];
  const vw = 2.95, vy = 1.95, vh = 2.5, vgap = 0.225;
  vids.forEach((v, i) => {
    const x = 0.35 + i * (vw + vgap);
    // 프레임
    s.addShape(pres.shapes.RECTANGLE, {
      x, y: vy, w: vw, h: vh,
      fill: { color: "06121E" }, line: { color: v.accent, width: 1.5 }
    });
    // 상단 라벨바
    s.addShape(pres.shapes.RECTANGLE, {
      x, y: vy, w: vw, h: 0.42,
      fill: { color: v.accent }, line: { color: v.accent }
    });
    s.addText(v.name, {
      x: x + 0.15, y: vy + 0.05, w: vw - 0.3, h: 0.32,
      fontFace: ENG, fontSize: 14, bold: true,
      color: i === 0 ? WHITE : NAVY, margin: 0
    });
    // 재생 아이콘 (원 + ▶)
    s.addShape(pres.shapes.OVAL, {
      x: x + vw / 2 - 0.4, y: vy + 0.95, w: 0.8, h: 0.8,
      fill: { color: NAVY2 }, line: { color: v.accent, width: 1.5 }
    });
    s.addText("▶", {
      x: x + vw / 2 - 0.4, y: vy + 0.95, w: 0.8, h: 0.8,
      fontFace: ENG, fontSize: 22, color: v.accent,
      align: "center", valign: "middle", margin: 0
    });
    // 하단 캡션
    s.addText(v.sub, {
      x: x + 0.1, y: vy + vh - 0.5, w: vw - 0.2, h: 0.28,
      fontFace: KOR, fontSize: 11, bold: true, color: WHITE,
      align: "center", margin: 0
    });
    s.addText("SUMO-GUI 녹화 영상", {
      x: x + 0.1, y: vy + vh - 0.26, w: vw - 0.2, h: 0.22,
      fontFace: KOR, fontSize: 8.5, italic: true, color: MUTED,
      align: "center", margin: 0
    });
  });

  // 하단 안내 배너
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.35, y: 4.7, w: 9.3, h: 0.62,
    fill: { color: NAVY2 }, line: { color: TEAL, width: 1 }
  });
  s.addText([
    { text: "추천 시나리오  ", options: { fontSize: 11, bold: true, color: CYAN, fontFace: KOR } },
    { text: "asymmetric(비대칭) — 세 정책의 차이가 가장 극적 (Fixed 1626 → SmartSignal 356, +78%).  ",
      options: { fontSize: 11, color: WHITE, fontFace: KOR } },
    { text: "각 프레임에 녹화 영상을 삽입.",
      options: { fontSize: 11, italic: true, color: MUTED, fontFace: KOR } },
  ], {
    x: 0.55, y: 4.7, w: 9.0, h: 0.62, valign: "middle", margin: 0
  });

  footer(s);
}

// ───────── 4. 3개 모델 시나리오 비교표 ─────────
{
  const s = pres.addSlide();
  s.background = { color: NAVY };
  pageHeader(s, 3, "3개 모델 시나리오 비교표",
             "5개 시나리오 × 평균 대기시간 · Fixed 대비 개선율");

  // 좌측: 비교표 삽입 영역 (placeholder)
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.35, y: 1.35, w: 6.0, h: 3.95,
    fill: { color: NAVY2 }, line: { color: CYAN, width: 1.5, dashType: "dash" }
  });
  s.addText("▶  비교표 삽입 영역", {
    x: 0.35, y: 2.85, w: 6.0, h: 0.45,
    fontFace: KOR, fontSize: 18, bold: true, color: CYAN,
    align: "center", margin: 0
  });
  s.addText("5 시나리오  ×  Fixed / Webster / SmartSignal\n(low · medium · high · asymmetric · saturated)", {
    x: 0.35, y: 3.35, w: 6.0, h: 0.7,
    fontFace: KOR, fontSize: 11, color: MUTED,
    align: "center", margin: 0
  });

  // 우측: 해석 박스
  const insights = [
    {
      accent: CYAN,
      ttl: "◆  비대칭 · 과포화가 핵심",
      desc: "수요가 한쪽으로 쏠리거나 용량을 초과할 때, 고정신호의 최선인 Webster조차 SmartSignal을 못 따라옴 (asymmetric +78%, saturated +20%)."
    },
    {
      accent: TEAL,
      ttl: "◆  균형 잡힌 해석",
      desc: "low · high 구간은 Webster가 우수함을 솔직히 인정 → 한쪽으로 치우치지 않은 분석으로 신뢰도를 높임."
    },
  ];
  insights.forEach((ins, i) => {
    const y = 1.35 + i * 2.0;
    s.addShape(pres.shapes.RECTANGLE, {
      x: 6.6, y, w: 3.05, h: 1.8,
      fill: { color: NAVY2 }, line: { color: ins.accent, width: 1 }
    });
    s.addText(ins.ttl, {
      x: 6.78, y: y + 0.12, w: 2.75, h: 0.35,
      fontFace: KOR, fontSize: 12, bold: true, color: ins.accent, margin: 0
    });
    s.addText(ins.desc, {
      x: 6.78, y: y + 0.52, w: 2.75, h: 1.15,
      fontFace: KOR, fontSize: 10, color: WHITE, margin: 0
    });
  });

  footer(s);
}

// ───────── 5. 카메라의 대안 — 가상 센서 ─────────
{
  const s = pres.addSlide();
  s.background = { color: NAVY };
  pageHeader(s, 4, "카메라의 대안 — 가상 센서",
             "CCTV 인식의 한계를 시뮬레이션 내부 센서로 우회");

  // 좌측: 막힌 길 (As-was)
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.35, y: 1.35, w: 3.9, h: 3.0,
    fill: { color: NAVY2 }, line: { color: AMBER, width: 1 }
  });
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.35, y: 1.35, w: 3.9, h: 0.5,
    fill: { color: AMBER }, line: { color: AMBER }
  });
  s.addText("막힌 길  ·  CCTV 폐루프", {
    x: 0.5, y: 1.43, w: 3.6, h: 0.35,
    fontFace: KOR, fontSize: 13, bold: true, color: NAVY, margin: 0
  });
  s.addText("As-was", {
    x: 0.5, y: 1.97, w: 3.6, h: 0.28,
    fontFace: ENG, fontSize: 11, italic: true, color: MUTED, margin: 0
  });
  s.addText([
    { text: "교차로 4방향 CCTV가 모두 존재하지 않음", options: { bullet: { code: "2716" }, breakLine: true } },
    { text: "공개 ITS API는 고속도로 · 국도 위주", options: { bullet: { code: "2716" }, breakLine: true } },
    { text: "차량 카운트 → 신호제어로 잇는 폐루프가 데이터상 불가", options: { bullet: { code: "2716" } } },
  ], {
    x: 0.55, y: 2.35, w: 3.55, h: 1.85,
    fontFace: KOR, fontSize: 11, color: WHITE, paraSpaceAfter: 8, margin: 0
  });

  // 화살표
  s.addText("▶", {
    x: 4.28, y: 2.6, w: 0.45, h: 0.5,
    fontFace: ENG, fontSize: 22, bold: true, color: CYAN,
    align: "center", valign: "middle", margin: 0
  });

  // 우측: 찾은 우회로 (To-be) — 헤더 + 2카드
  s.addText("찾은 우회로  ·  가상 센서  (To-be)", {
    x: 4.8, y: 1.3, w: 4.85, h: 0.35,
    fontFace: KOR, fontSize: 13, bold: true, color: CYAN, margin: 0
  });

  const sensors = [
    {
      tag: "E2",
      name: "차로 영역 감지기",
      desc: "특정 차로 구간의 대기열 길이 · 차량 수 · 점유율을 직접 측정 → 카메라가 “세려던” 값을 오차 없이 제공"
    },
    {
      tag: "TraCI",
      name: "실시간 제어 API",
      desc: "시뮬레이션 상태(차량 위치 · 대기시간)를 실시간 질의 → RL state로 주입, 인식 단계 없이 폐루프 완성"
    },
  ];
  sensors.forEach((se, i) => {
    const y = 1.72 + i * 1.32;
    card(s, 4.8, y, 4.85, 1.18, CYAN);
    s.addShape(pres.shapes.RECTANGLE, {
      x: 4.95, y: y + 0.18, w: 0.95, h: 0.82,
      fill: { color: NAVY }, line: { color: CYAN, width: 1 }
    });
    s.addText(se.tag, {
      x: 4.95, y: y + 0.18, w: 0.95, h: 0.82,
      fontFace: ENG, fontSize: 14, bold: true, color: CYAN,
      align: "center", valign: "middle", margin: 0
    });
    s.addText(se.name, {
      x: 6.05, y: y + 0.13, w: 3.5, h: 0.32,
      fontFace: KOR, fontSize: 12, bold: true, color: WHITE, margin: 0
    });
    s.addText(se.desc, {
      x: 6.05, y: y + 0.45, w: 3.5, h: 0.65,
      fontFace: KOR, fontSize: 9.5, color: MUTED, margin: 0
    });
  });

  // 하단: 2학기 연결 배너
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.35, y: 4.6, w: 9.3, h: 0.72,
    fill: { color: NAVY2 }, line: { color: GREEN, width: 1 }
  });
  s.addText([
    { text: "2학기 연결   ", options: { fontSize: 12, bold: true, color: GREEN, fontFace: KOR } },
    { text: "가상 센서 = 실제 도로의 ", options: { fontSize: 12, color: WHITE, fontFace: KOR } },
    { text: "루프검지기 · 매설센서", options: { fontSize: 12, bold: true, color: CYAN, fontFace: KOR } },
    { text: "에 대응 → 실증 확장 시 그대로 이어지는 설계",
      options: { fontSize: 12, color: WHITE, fontFace: KOR } },
  ], {
    x: 0.55, y: 4.6, w: 9.0, h: 0.72, valign: "middle", margin: 0
  });

  footer(s);
}

// ───────── 저장 ─────────
const OUT = "C:/Users/jinuk/Desktop/4학년 1학기/ICT종합설계1/발표자료/ICT종합설계_11차발표전형주이진욱.pptx";
pres.writeFile({ fileName: OUT }).then(() => {
  console.log("[OK] saved:", OUT);
});
