import { Component, type ErrorInfo, type ReactNode } from 'react';
import { askHref } from '../lib/route';
import { Icon } from './Icon';

/**
 * 화면 하나(생애주기·관계 지도)나 상담 화면의 한 영역(답변·정책 목록)을 그리다 오류가 나도 앱 전체가 하얗게 비지 않게 막는다.
 * 데이터 형식이 예상과 달라도 상담 화면은 계속 쓸 수 있어야 한다 (CODING_STYLE §9: 실패를 숨기지 않고 예측 가능하게 처리).
 * resetKey가 바뀌면(다른 화면·정책으로 이동) 다시 그려 본다.
 */
interface Props {
  resetKey: string;
  children: ReactNode;
  /** 상담 화면 안의 한 영역(답변 한 건·정책 목록)처럼 화면 전체가 아닌 곳에서 쓸 대체 내용. 없으면 화면 단위 안내를 보여 준다 */
  fallback?: ReactNode;
}

export class ErrorBoundary extends Component<Props, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // 화면 정보만 남긴다. 사용자 입력·답변 내용은 넣지 않는다
    console.error('화면을 그리지 못했어요:', error.name, info.componentStack?.split('\n')[1]?.trim());
  }

  componentDidUpdate(prev: { resetKey: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.failed) this.setState({ failed: false });
  }

  render() {
    if (!this.state.failed) return this.props.children;
    if (this.props.fallback !== undefined) return this.props.fallback;
    return (
      <section className="board">
        <div className="wrap">
          <div className="notice bad">
            <Icon name="warning" />
            <div><b>이 화면을 보여 드리지 못했어요</b><p>데이터를 읽는 중에 문제가 생겼어요. 상담 화면은 그대로 쓸 수 있어요.</p></div>
          </div>
          <a className="btn btn-o err-back" href={askHref}>상담 화면으로</a>
        </div>
      </section>
    );
  }
}

/** 상담 화면 안의 한 영역을 그리지 못했을 때의 안내. section이면 화면 폭 영역(board)으로 감싼다 */
export function RegionError({ title, detail, section = false }: { title: string; detail: string; section?: boolean }) {
  const notice = (
    <div className="notice bad" role="alert">
      <Icon name="warning" />
      <div><b>{title}</b><p>{detail}</p></div>
    </div>
  );
  return section ? <section className="board"><div className="wrap">{notice}</div></section> : notice;
}
