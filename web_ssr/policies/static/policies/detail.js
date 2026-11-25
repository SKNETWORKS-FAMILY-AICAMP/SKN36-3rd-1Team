/*
 * 제출서류 더보기/접기 — 서버가 렌더링한 HTML 위에 붙는 작은 상호작용.
 * 서류 내용은 처음부터 HTML에 모두 들어 있다. 이 스크립트가 실행되지 않으면 서류가 전부 펼쳐진 채로 보인다.
 */
(function () {
  'use strict';
  var COLLAPSED_HEIGHT = 220; // px, detail.css의 .docs-body.is-collapsed max-height와 같은 값

  function setup() {
    var body = document.querySelector('[data-docs-body]');
    var button = document.querySelector('[data-docs-toggle]');
    if (!body || !button) return;
    // 접을 만큼 길지 않으면 버튼을 보여 주지 않는다.
    if (body.scrollHeight <= COLLAPSED_HEIGHT + 40) return;

    function render(expanded) {
      body.classList.toggle('is-collapsed', !expanded);
      button.setAttribute('aria-expanded', expanded ? 'true' : 'false');
      button.textContent = expanded ? '접기' : '더보기';
    }

    render(false);
    button.hidden = false;
    button.addEventListener('click', function () {
      var expanded = button.getAttribute('aria-expanded') === 'true';
      render(!expanded);
      if (expanded) body.scrollIntoView({ block: 'nearest' });
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', setup);
  else setup();
})();
