/**
 * 정책 하나를 가운데 두고 이웃을 둘레에 놓는 원형 배치.
 * 그래프 라이브러리를 새로 설치하지 않고(AGENTS.md §5) 같은 종류끼리 모아 각도만 나눠 준다.
 */
import { KIND_ORDER } from '../config/graph';
import type { Neighbor } from './graphModel';

/** 지도를 페이지 위쪽에 가로로 넓게 쓰므로 가로가 긴 판에 타원으로 놓는다 */
export const MAP_WIDTH = 1000;
export const MAP_HEIGHT = 540;
const CENTER = { x: MAP_WIDTH / 2, y: MAP_HEIGHT / 2 };
const RADIUS_OUTER = 205;
const RADIUS_INNER = 145;
/** 가로 방향으로 더 벌려 이름이 덜 겹치게 한다 */
const WIDE = 1.75;
/** 이웃이 이보다 많으면 안쪽·바깥쪽 원을 번갈아 써서 이름이 겹치지 않게 한다 */
const TWO_RING_FROM = 12;

/** 원 크기·이름 위치 (viewBox 단위). 가운데 정책 원은 크게, 이름은 원 아래 LABEL_GAP 만큼 띄운다 */
export const NODE_R = 30;
export const CENTER_R = 46;
export const LABEL_GAP = 16;

export interface PlacedNeighbor extends Neighbor {
  x: number;
  y: number;
}

export function layoutNeighbors(neighbors: Neighbor[]): { center: { x: number; y: number }; placed: PlacedNeighbor[] } {
  const rank = (n: Neighbor) => {
    const i = KIND_ORDER.indexOf(n.node.kind);
    return i < 0 ? KIND_ORDER.length : i;
  };
  const sorted = [...neighbors].sort((a, b) => rank(a) - rank(b) || a.node.label.localeCompare(b.node.label, 'ko'));
  const twoRings = sorted.length >= TWO_RING_FROM;
  const placed = sorted.map((n, i) => {
    // 12시 방향부터 시계 방향으로 고르게 나눈다
    const angle = -Math.PI / 2 + (2 * Math.PI * i) / Math.max(sorted.length, 1);
    const r = twoRings && i % 2 === 1 ? RADIUS_INNER : RADIUS_OUTER;
    return { ...n, x: CENTER.x + r * WIDE * Math.cos(angle), y: CENTER.y + r * Math.sin(angle) };
  });
  return { center: CENTER, placed };
}

/**
 * 지도 보기 상태: 지도 좌표(viewBox 단위)에서 scale배 확대하고 (x, y)만큼 옮긴다.
 * 화면에는 <g transform="translate(x y) scale(scale)">로 그린다.
 */
export interface MapView { scale: number; x: number; y: number }
export const INITIAL_VIEW: MapView = { scale: 1, x: 0, y: 0 };

/**
 * 한 점(지도 좌표)을 기준으로 확대·축소한다. 그 점이 화면에서 제자리에 있도록 이동값을 함께 바꾼다.
 * 배율은 [min, max] 안으로 자른다.
 */
export function zoomAt(view: MapView, factor: number, point: { x: number; y: number }, min: number, max: number): MapView {
  const scale = Math.min(max, Math.max(min, view.scale * factor));
  const ratio = scale / view.scale;
  return { scale, x: point.x - (point.x - view.x) * ratio, y: point.y - (point.y - view.y) * ratio };
}

/** 지도 가운데를 기준으로 확대·축소 (버튼용) */
export const zoomCenter = (view: MapView, factor: number, min: number, max: number) =>
  zoomAt(view, factor, { x: MAP_WIDTH / 2, y: MAP_HEIGHT / 2 }, min, max);

/**
 * 지도에 쓸 이름들. 잘랐을 때 서로 같아지는 이름끼리는 공통 앞부분(띄어쓰기 단위)을 떼어 다른 부분이 보이게 한다.
 * 예: '고용노동부 국민취업지원제도 정부24 …', '고용노동부 국민취업지원제도 온통청년 …' → '…정부24 …', '…온통청년 …'
 * 전체 이름은 원에 마우스를 올리거나 팝업에서 보여 준다.
 */
export function distinctLabels(labels: string[], max: number): string[] {
  const text = [...labels];
  // 앞을 뗀 이름은 '…'을 붙여 보여 주므로, 같은지 비교도 화면에 보일 모습으로 한다
  const shown = (i: number) => shortLabel(text[i] === labels[i] ? text[i] : `…${text[i]}`, max);
  // 한 번 떼고 나서도 또 같아질 수 있어(기관 → 제도명 → 문서 출처) 몇 번 반복한다
  for (let round = 0; round < DISTINCT_ROUNDS; round += 1) {
    const groups = new Map<string, number[]>();
    text.forEach((_, i) => {
      const key = shown(i);
      const list = groups.get(key);
      if (list) list.push(i); else groups.set(key, [i]);
    });
    let changed = false;
    for (const idx of groups.values()) {
      // 완전히 같은 이름끼리는 떼어도 구분되지 않으니 그대로 둔다
      if (idx.length < 2 || new Set(idx.map((i) => text[i])).size === 1) continue;
      const cut = commonWordPrefix(idx.map((i) => text[i]));
      if (cut === 0 || idx.some((i) => text[i].length <= cut)) continue;
      for (const i of idx) text[i] = text[i].slice(cut);
      changed = true;
    }
    if (!changed) break;
  }
  return text.map((_, i) => shown(i));
}
const DISTINCT_ROUNDS = 3;

/** 여러 이름의 공통 앞부분 길이 (마지막 띄어쓰기까지만 자른다. 단어 중간에서 자르지 않게) */
function commonWordPrefix(texts: string[]): number {
  const first = texts[0];
  let n = 0;
  while (n < first.length && texts.every((t) => t[n] === first[n])) n += 1;
  const space = first.lastIndexOf(' ', n - 1);
  return space >= 0 ? space + 1 : 0;
}

/** 지도에 쓸 짧은 이름 */
export function shortLabel(label: string, max: number): string {
  const chars = [...label];
  return chars.length > max ? `${chars.slice(0, max - 1).join('')}…` : label;
}
