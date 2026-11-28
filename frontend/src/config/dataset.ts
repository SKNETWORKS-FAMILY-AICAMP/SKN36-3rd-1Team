/**
 * 화면이 읽는 팀 기준 데이터 버전. 백엔드 LIFEPOLICY_DATASET_DIR 기본값과 같은 폴더다.
 * 버전이 바뀌면 이 값과 graph/loadGraph.ts의 import.meta.glob 경로 두 곳을 같이 고친다
 * (Vite의 glob은 상수를 쓸 수 없고 문자열을 직접 써야 한다). 둘이 어긋나면 loadGraph.test.ts가 실패한다.
 */
export const DATASET_VERSION = 'v1.4.0-candidate';
/** 저장소 루트 기준 데이터 폴더 */
export const DATASET_DIR = `data/releases/${DATASET_VERSION}`;
