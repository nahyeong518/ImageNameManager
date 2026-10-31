ImageNameManager 바탕화면 설치형 프로그램 빌드

이 ZIP에는 완성된 Setup.exe가 아니라 Windows에서 설치 파일을 생성하는 빌드 도구가 들어 있습니다.
일반 사용자에게 배포할 때는 빌드 후 Output\ImageNameManager_Setup.exe 파일만 전달하면 됩니다.
설치하는 사람은 Python이나 Inno Setup을 설치할 필요가 없습니다.

빌드 PC 준비
1. Windows 10/11
2. Python (빌드용)
3. Inno Setup 6 (빌드용)

빌드 방법
1. ZIP 압축을 풉니다.
2. build_installer.bat을 실행합니다.
3. Output 폴더의 ImageNameManager_Setup.exe를 배포합니다.

설치 위치 및 데이터
- 프로그램 전체가 현재 사용자 바탕화면의 ImageNameManager 폴더에 설치됩니다.
- ImageNameData도 프로그램 폴더 안에 저장됩니다.
- 이전 버전의 데이터가 %LOCALAPPDATA%\Programs\ImageNameManager\ImageNameData에 있고 새 바탕화면 폴더에 데이터가 없으면, 첫 실행 시 자동으로 복사합니다.
- 자동 복사는 원본 데이터를 삭제하거나 이동하지 않습니다.
- 기존 데이터는 설치 전 별도 백업을 권장합니다.
- 설치 프로그램은 현재 사용자 권한으로 설치되며 관리자 권한이 필요하지 않습니다.

주의
바탕화면이 OneDrive 등으로 동기화되는 환경에서는 폴더 경로가 다를 수 있습니다. 이 경우 설치 후 실제 생성 위치를 확인하세요.

검사 범위
Python 문법 및 ZIP 무결성은 검사할 수 있습니다. 실제 Windows EXE 빌드, 설치, 기존 데이터 자동 이전은 Windows PC에서 확인해야 합니다.
