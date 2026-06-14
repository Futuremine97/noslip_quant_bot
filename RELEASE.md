# 데스크톱 릴리스 (dmg / exe)

GitHub Releases로 macOS `.dmg`와 Windows `.exe` 설치 파일을 자동 배포합니다.
사용자는 받아서 **일반 앱처럼 설치**합니다(터미널 불필요).

## 1. 릴리스 만드는 법 (메인테이너)

버전 태그를 푸시하면 `.github/workflows/release.yml`이 자동으로
macOS(arm64+x64) dmg와 Windows x64 exe를 빌드해 Release에 첨부합니다.

```bash
# 1) 버전 올리기 (package.json "version")
# 2) 태그 푸시
git tag v0.1.0
git push origin v0.1.0
```

- `Actions` 탭에서 빌드 진행을 확인합니다(약 10~20분).
- 끝나면 `Releases`에 dmg/exe가 첨부된 릴리스가 생성됩니다.
- 수동 실행: Actions → "Build & Release Desktop App" → Run workflow (이 경우 아티팩트만,
  태그가 아니므로 Release 게시는 생략).

### 로컬에서 직접 빌드
```bash
npm install
npm run build                 # next build
npx electron-builder --mac dmg --arm64 --x64   # macOS
npx electron-builder --win nsis --x64          # Windows(윈도우에서)
# 결과물: dist-desktop/
```
편의 스크립트: `npm run desktop:build`(mac+win), `npm run desktop:build:mac`.

## 2. 설치 방법 (사용자)

| OS | 파일 | 설치 |
|---|---|---|
| macOS (Apple Silicon) | `NoSlipQuant-*-arm64.dmg` | 더블클릭 → Applications로 드래그 |
| macOS (Intel) | `NoSlipQuant-*-x64.dmg` | 더블클릭 → Applications로 드래그 |
| Windows 10/11 (x64) | `NoSlipQuant Setup *.exe` | 실행 → 설치 마법사(설치 경로 선택 가능) |

앱을 켜면 내장 Next.js 대시보드 + API 서버가 자동 시작되고, **메뉴바(트레이) 아이콘**에서
터미널 통합 제어와 관리 화면(/manage)에 접근할 수 있습니다.

### ⚠️ 코드서명 미적용 안내
무료 배포라 Apple/Microsoft 코드서명을 넣지 않았습니다. 첫 실행만 한 단계가 더 필요합니다.

- **macOS**: 앱을 **우클릭 → 열기 → 열기**. 또는 시스템 설정 → 개인정보 보호 및 보안 →
  하단의 "확인 없이 열기". (Gatekeeper가 미서명 앱을 처음 한 번 차단합니다.)
- **Windows**: SmartScreen 창에서 **추가 정보 → 실행**.

> 정식 서명/공증을 원하면 Apple Developer ID, Windows EV 인증서를 발급받아
> 워크플로에 `CSC_LINK`/`CSC_KEY_PASSWORD`(mac), 윈도우 서명 시크릿을 추가하면 됩니다.

## 3. 환경 변수
- `NOSLIP_PORT`(기본 3000), `NOSLIP_API_PORT`(기본 8787)
- `NOSLIP_DASHBOARD_URL` — 원격 대시보드에 연결할 때
- Python 트레이딩 봇·텔레그램 데몬은 데스크톱 앱과 별개로 실행합니다(README 참고).
