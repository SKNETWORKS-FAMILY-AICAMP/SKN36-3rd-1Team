import { Component, type ErrorInfo, type ReactNode } from 'react';
import { askHref } from '../lib/route';
import { Icon } from './Icon';

/**
 * 화면 하나(생애주기·관계 지도)를 그리다 오류가 나도 앱 전체가 하얗게 비지 않게 막는다.
 * 데이터 형식이 예상과 달라도 상담 화면은 계속 쓸 수 있어야 한다 (CODING_STYLE §9: 실패를 숨기지 않고 예측 가능하게 처리).
 * resetKey가 바뀌면(다른 화면·정책으로 이동) 다시 그려 본다.
 */
export class ErrorBoundary extends Component<{ resetKey: string; children: ReactNode }, { failed: boolean }> {
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
