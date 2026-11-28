/**
 * 정책 원문 글에 남아 있는 HTML 문자 코드(&#9312; → ①, &rsquo; → ’ 등)를 화면에 보일 글자로 바꾼다.
 *
 * 수집한 웹 원문을 저장할 때 코드가 풀리지 않은 채 들어갔다(v1.4.0-candidate 기준 정책 29개의 지원 내용·대상).
 * 데이터는 #18 데이터 동결에 따라 고치지 않고, 화면에 들어오는 지점(그래프 데이터 읽기·API 응답)에서만 바꾼다.
 * React는 글자를 그대로 그리므로 '<' 같은 글자가 나와도 HTML로 해석되지 않는다.
 */

/** 데이터에 실제로 나온 이름(rsquo·lsquo·times·sim·divide·rarr·ge·harr·middot)과 흔히 쓰는 몇 개만 둔다. 모르는 이름은 그대로 둔다 */
const NAMED_ENTITIES: Record<string, string> = {
  amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ',
  lsquo: '‘', rsquo: '’', ldquo: '“', rdquo: '”', middot: '·', hellip: '…',
  times: '×', divide: '÷', sim: '∼', ge: '≥', le: '≤', rarr: '→', larr: '←', harr: '↔',
};
const ENTITY = /&(#\d{1,7}|#[xX][0-9a-fA-F]{1,6}|[a-zA-Z]{2,8});/g;
const MAX_CODE_POINT = 0x10ffff;
const SURROGATE_START = 0xd800, SURROGATE_END = 0xdfff;
/** 탭·줄바꿈을 뺀 제어 문자는 글자로 바꾸지 않는다(눈에 안 보이는 문자를 만들지 않기 위해) */
const FIRST_PRINTABLE = 0x20;
const ALLOWED_CONTROLS = new Set([0x09, 0x0a, 0x0d]);

function numericEntity(body: string): string | null {
  const hex = body[1] === 'x' || body[1] === 'X';
  const code = Number.parseInt(body.slice(hex ? 2 : 1), hex ? 16 : 10);
  if (!Number.isInteger(code) || code <= 0 || code > MAX_CODE_POINT) return null;
  if (code >= SURROGATE_START && code <= SURROGATE_END) return null;
  if (code < FIRST_PRINTABLE && !ALLOWED_CONTROLS.has(code)) return null;
  return String.fromCodePoint(code);
}

export function decodeHtmlEntities(text: string): string {
  if (!text.includes('&')) return text;
  return text.replace(ENTITY, (whole, body: string) =>
    (body.startsWith('#') ? numericEntity(body) : NAMED_ENTITIES[body]) ?? whole);
}

/** 주소 값은 글자를 바꾸지 않는다(검증된 원문 주소를 그대로 연다) */
const isUrlKey = (key: string) => key === 'url' || key.endsWith('_url');

/** 객체·배열 안의 문자열을 모두 바꾼다. 주소 필드(url, *_url)는 그대로 둔다 */
export function decodeEntitiesDeep<T>(value: T): T {
  if (typeof value === 'string') return decodeHtmlEntities(value) as T;
  if (Array.isArray(value)) return value.map((item) => decodeEntitiesDeep(item)) as T;
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) =>
      [key, isUrlKey(key) ? item : decodeEntitiesDeep(item)])) as T;
  }
  return value;
}
