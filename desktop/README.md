# No Slip Quant Desktop (dmg / exe)

Next.js 대시보드를 네이티브 창으로 감싸는 Electron 앱. 서버 미기동 시 시작 안내 + 5초 자동 재연결.

## 릴리스 (자동)

태그를 푸시하면 GitHub Actions가 macOS dmg + Windows exe를 빌드해 Release에 첨부한다:

```bash
git tag v1.1.0
git push origin v1.1.0
```

수동 실행: GitHub → Actions → "Build & Release Desktop App" → Run workflow.

## 로컬 개발

```bash
cd desktop
npm install
npm start                # 개발 실행
npm run dist:mac         # dmg 빌드 (macOS에서)
npm run dist:win         # exe 빌드 (Windows에서)
```

## 설정

- 대시보드 주소: 기본 `http://localhost:3000`, `NOSLIP_DASHBOARD_URL` 환경변수로 변경
- 아이콘: `build/icon.png` (512x512, electron-builder가 icns/ico 자동 생성)
- 코드서명: 미적용 (필요 시 `CSC_LINK`/`WIN_CSC_LINK` 시크릿 추가)
