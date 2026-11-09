Market Monitor - 집 PC용 패키지
================================

[A] 화면만 보기 (Python/키 불필요)
  index.html 을 더블클릭하세요. (같은 폴더의 data.js 가 마지막 수집 시점 데이터입니다)

[B] 집에서 데이터 직접 수집하기
  1. Python 3.10 이상 설치 (python.org, 설치 시 "Add python.exe to PATH" 체크)
  2. run_collect.bat 더블클릭
     - 처음 실행하면 .env 파일이 만들어지고 메모장이 열립니다.
     - 아래 값을 채우고 저장한 뒤 run_collect.bat 을 다시 실행하세요.
         KRX_ID=        (data.krx.co.kr 계정)
         KRX_PW=
         ECOS_API_KEY=  (ecos.bok.or.kr 인증키)
         FRED_API_KEY=  (fredaccount.stlouisfed.org/apikeys)
     - 필요한 라이브러리 설치 -> 수집(약 2분) -> data.js 갱신
  3. index.html 을 새로고침(Ctrl+F5)

[주의]
  - .env 에는 비밀번호/키가 들어갑니다. 메일·메신저·공유폴더·OneDrive 에 올리지 마세요.
  - 이 패키지에는 .env 와 회사용 인증서(certs)가 포함되어 있지 않습니다.
  - 화면의 "샘플" 태그 항목은 난수 데이터입니다. (신용잔고·예탁금·반대매매, 선행PER, VKOSPI 등)
  - 집에서 freesis.kofia.or.kr 이 열리는지 확인해 보세요. 열리면 신용잔고 자동수집을 시도할 수 있습니다.
