VEXO / ALL IN ONE DEAL 웹사이트 수정 적용

1) 기존 웹사이트 폴더를 이번 폴더 파일로 교체하세요.
2) 기존 .env는 유지하세요.
3) OWNER_DISCORD_ID는 반드시 "서버 ID"가 아니라 본인의 Discord 사용자 ID여야 합니다.
4) 오너 로그인 계정과 OWNER_DISCORD_ID가 정확히 같을 때만 /dashboard가 표시되고 접근됩니다.
5) 대시보드에서 상품 추가, 이미지 업로드, 가격/재고 수정, 코드 직접 추가, 코드 자동 생성이 가능합니다.
6) 이미지 파일은 static/uploads/products 에 저장되며 상품 JSON에는 상대 경로가 기록됩니다.
7) MAX_PRODUCT_IMAGE_MB=8 을 .env에 추가하면 업로드 용량을 제한할 수 있습니다.
8) 이미지 로딩은 외부 의존성을 최소화하기 위해 기본 상품을 로컬 WebP로 제공하며, 서버 부스트는 웹 원본 이미지를 사용합니다.
