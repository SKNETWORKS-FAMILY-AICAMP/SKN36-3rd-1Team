import type { Persona } from '../config/personas';
import { Icon } from './Icon';
import { MascotFace } from './Logo';

/**
 * 상담 결과가 비어 있을 때 보여 주는 첫 안내.
 * 첫 화면 위쪽(검색창·분야 카드)을 복잡하게 하지 않도록 대표 상황 카드는 이 빈 자리에 두고,
 * 첫 질문을 하면 대화에 자리를 내준다.
 */
export function PersonaStart({ personas, onPersona }: {
  personas: Persona[];
  onPersona: (persona: Persona, origin: HTMLElement | null) => void;
}) {
  return (
    <div className="empty start">
      <MascotFace />
      <b>아직 질문이 없어요</b>
      <span>위 검색창에 물어보거나 분야 카드를 눌러 보세요. 이런 상황이라면 아래 카드로 바로 시작할 수 있어요.</span>
      <div className="persona-row" aria-label="대표 상황으로 시작하기">
        {personas.map((p) => (
          <button key={p.key} type="button" className="persona" onClick={(e) => onPersona(p, e.currentTarget.querySelector('.ic'))}>
            <Icon name={p.icon} className="ic" />
            <span><b>{p.title}</b><small>{p.situation}</small><em>“{p.question}”</em></span>
          </button>
        ))}
      </div>
      <small className="persona-note">PRD 대표 시나리오의 가상 인물이에요</small>
    </div>
  );
}
