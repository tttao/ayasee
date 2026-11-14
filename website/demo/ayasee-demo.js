/* ayasee demo. Steps and page wiring; the engine is vendor/step-window.js (MIT, see vendor/step-window.LICENSE.txt). */
/* ayasee demo: five steps on the html-demo engine (vanilla). Every frame is a pure function of t.
   All terminal text is real ayasee 0.1.0-rc.3 output (init, doctor, ledger show). The GitHub screens use
   the real job names (resolve, plan-first-stage, plan-dev, prod-plan, prod) and the real "Prod gate" summary.
   Prefix: ay- (classes) */
(function () {
  const { ease, typed, glide, cue } = HTMLDemo;
  const clamp01 = (x) => Math.min(1, Math.max(0, x));
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

  /* ---------- terminal helpers ---------- */
  // a line: [at, html, off?]; `at` is when it appears, `off` when it is cleared
  const ln = (at, html, off) => `<div class="ay-ln" data-at="${at}"${off != null ? ` data-off="${off}"` : ''}>${html || '&nbsp;'}</div>`;
  const cmd = (id, text, a, b, off) =>
    `<div class="ay-ln ay-prompt" data-at="${a - 0.25}"${off != null ? ` data-off="${off}"` : ''}><span class="ay-ps">$</span> <span class="ay-cmd" id="${id}" data-text="${esc(text)}" data-a="${a}" data-b="${b}"></span><i class="ay-caret" data-caret="${id}"></i></div>`;
  const ok = (s) => `  <b class="ay-ok">ok</b>    ${esc(s)}`;
  const fail = (s) => `  <b class="ay-fail">FAIL</b>  ${esc(s)}`;
  const h = (s) => `<span class="ay-h">${esc(s)}</span>`;
  const dim = (s) => `<span class="ay-dim">${esc(s)}</span>`;

  function drawTerm(t, $) {
    const root = $('.ay-term');
    root.querySelectorAll('[data-at]').forEach((el) => {
      const at = +el.dataset.at, off = el.dataset.off != null ? +el.dataset.off : Infinity;
      el.style.display = t >= at && t < off ? 'block' : 'none';
    });
    root.querySelectorAll('.ay-cmd').forEach((el) => {
      const done = t >= +el.dataset.b;
      el.textContent = typed(el.dataset.text, t, +el.dataset.a, +el.dataset.b);
      const caret = root.querySelector(`[data-caret="${el.id}"]`);
      // the caret blinks while typing or waiting, then leaves when output starts
      caret.style.visibility = !done || (t < +el.dataset.b + 0.35 && Math.floor(t * 2.4) % 2 === 0) ? 'visible' : 'hidden';
      if (t < +el.dataset.a) caret.style.visibility = 'hidden';
    });
  }
  const term = (inner, title) => `<div class="ay-term"><div class="ay-tt">${esc(title)}</div><div class="ay-body">${inner}</div></div>`;

  /* ---------- step 1: scaffold ---------- */
  const INIT = 'uvx ayasee@0.1.0-rc.3 init --profile cloudflare-s3 --iac-root infra/iac';
  const WROTE = ['.ayasee.yaml', '.github/workflows/iac-pr.yml', '.github/workflows/iac-staged.yml', '.github/workflows/iac-nightly.yml',
    '.github/workflows/iac-breakglass.yml', 'infra/iac/root.hcl', 'infra/iac/bootstrap/main.tf', 'infra/iac/bootstrap/.terraform.lock.hcl',
    'infra/iac/stages/test/.gitkeep', 'infra/iac/stages/prod/.gitkeep'];
  const WROTE_AT = 2.75, WROTE_STEP = 0.2;
  const step1 = {
    id: 'scaffold', name: 'Scaffold', dur: 7,
    html: term(
      cmd('c1', INIT, 0.6, 2.3) +
      WROTE.map((f, i) => ln(WROTE_AT + i * WROTE_STEP, `<span class="ay-dim">wrote</span> ${esc(f)}`)).join('') +
      ln(5.0, '') +
      ln(5.0, `Pinned to <span class="ay-pin" id="pin">tttao/ayasee@5a08fde90cf9b4f470d6499e7bc1a13737c1aa2b</span>.`) +
      ln(5.5, '<b>Next:</b>') +
      ln(5.7, '  1. run the bootstrap root in infra/iac/bootstrap (a repository administrator, locally)') +
      ln(5.9, '  2. create the state store (the profile\'s README), then the secrets: run one by one') +
      ln(6.1, '     the commands of `tofu output -raw secret_commands` in infra/iac/bootstrap'),
      'acme/platform: zsh'),
    draw(t, $) {
      drawTerm(t, $);
      $('#pin').classList.toggle('on', t >= 5.1);
      return { cursor: { x: 1000, y: 600, hide: true } };
    },
  };

  /* ---------- step 2: doctor ---------- */
  const FAILS = ['iac-test', 'iac-prod-plan', 'iac-prod', 'iac-test-plan', 'iac-prod-breakglass', 'iac-drift-test', 'iac-drift-prod'];
  const CLEAR = 4.6;
  const step2 = {
    id: 'doctor', name: 'Check', dur: 8,
    html: term(
      cmd('c2', 'GITHUB_TOKEN="$(gh auth token)" ayasee doctor', 0.4, 1.9, CLEAR) +
      ln(2.1, h('configuration'), CLEAR) + ln(2.1, ok('.ayasee.yaml loads (stages test, prod)'), CLEAR) + ln(2.1, '', CLEAR) +
      ln(2.25, h('callers'), CLEAR) + ln(2.25, ok('.github/workflows/iac-staged.yml (staged -> iac-staged): pinned to tttao/ayasee@5a08fde90cf9'), CLEAR) + ln(2.25, '', CLEAR) +
      ln(2.4, h('GitHub (acme/platform)'), CLEAR) +
      FAILS.map((e, i) => ln(2.5 + i * 0.2, fail(`environment ${e} is missing (run the bootstrap)`), CLEAR)).join('') + ln(4.0, '', CLEAR) +
      ln(4.0, '<b class="ay-fail">7 failed</b>, 3 warnings, 1 skipped', CLEAR) +
      ln(4.2, dim('# run the bootstrap, then again'), CLEAR) +
      cmd('c2b', 'ayasee doctor', 5.0, 5.7) +
      ln(5.95, h('configuration')) + ln(5.95, ok('.ayasee.yaml loads (stages test, prod)')) +
      ln(6.1, h('GitHub (acme/platform)')) +
      ln(6.1, ok('environments present: iac-test, iac-test-plan, iac-prod-plan, iac-prod, iac-prod-breakglass, iac-drift-test, iac-drift-prod')) +
      ln(6.3, ok('main has a pull-request ruleset (the runs check the ledger token\'s bypass)')) +
      ln(6.5, h('release pin')) + ln(6.5, ok('tttao/ayasee@5a08fde90cf9 is the release commit of 0.1.0-rc.3 (CLI 0.1.0-rc.3)')) +
      ln(6.9, '') + ln(6.9, '<b class="ay-ok">0 failed</b>, 0 warnings, 0 skipped'),
      'acme/platform: zsh'),
    draw(t, $) {
      drawTerm(t, $);
      return { cursor: { x: 1000, y: 600, hide: true } };
    },
  };

  /* ---------- GitHub-style chrome ---------- */
  const check = (c) => `<svg class="ay-ico" viewBox="0 0 16 16" style="--c:${c}"><path d="M3 8.5 6.5 12 13 4.5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  const gh = (inner, crumb, tab) => `<div class="ay-gh">
    <div class="ay-ghbar"><span class="ay-logo"></span><span class="ay-crumb">${crumb}</span></div>${inner}</div>`;

  /* ---------- step 3: pull request ---------- */
  const CHECKS = [['iac-pr / resolve', 0.9], ['iac-pr / static checks and scans', 1.3], ['iac-pr / plan-first-stage', 1.7], ['iac-pr / plan-dev', 2.1]];
  const C_IN = 3.0, C_OUT = 3.9, LOST_AT = 5.0;
  const step3 = {
    id: 'plan', name: 'Review plan', dur: 8,
    html: gh(`
      <div class="ay-pr">
        <h3 class="ay-title">Revoke analysts SELECT on prod grants <span class="ay-num">#42</span></h3>
        <div class="ay-sub"><span class="ay-badge">Open</span> <b>maria</b> wants to merge 1 commit into <code>main</code> from <code>revoke-analysts</code></div>
        <div class="ay-card ay-checks"><div class="ay-chead" id="chead">Some checks haven't completed yet</div>
          ${CHECKS.map(([n], i) => `<div class="ay-crow" data-i="${i}"><span class="ay-st"></span><span>${esc(n)}</span></div>`).join('')}</div>
        <div class="ay-card ay-comment" id="cmt">
          <div class="ay-chead"><b>github-actions</b> <span class="ay-bot">bot</span> commented</div>
          <div class="ay-md"><h4>Prod gate: 0 critical · 1 high · 0 medium · 1 changes</h4>
            <h5>High</h5>
            <ul><li><code>grants</code> <code>databricks_grants.x</code> (update) [dbx-access]
              <ul><li class="ay-lost" id="lost">lost: <code>analysts</code> SELECT</li></ul></li></ul></div></div>
      </div>`, 'acme / platform'),
    draw(t, $) {
      CHECKS.forEach(([, at], i) => {
        const row = $(`.ay-crow[data-i="${i}"]`);
        const done = t >= at, run = t >= at - 0.4 && !done;
        row.querySelector('.ay-st').innerHTML = done ? check('#1a7f37') : run ? `<i class="ay-spin" style="transform:rotate(${(t * 420) % 360}deg)"></i>` : '<i class="ay-dot"></i>';
        row.style.opacity = t >= at - 0.5 ? 1 : 0.35;
      });
      const all = t >= CHECKS[CHECKS.length - 1][1];
      $('#chead').innerHTML = all ? `<b class="ay-good">All checks have passed</b> <span class="ay-dim2">4 successful checks</span>` : "Some checks haven't completed yet";
      const k = ease(t, C_IN, C_OUT);
      const c = $('#cmt');
      c.style.opacity = k;
      c.style.transform = `translateY(${(1 - k) * 28}px)`;
      $('#lost').classList.toggle('on', t >= LOST_AT);
      const p = glide(t, [[0, 0, 1000, 700], [2.6, 3.6, 560, 470], [4.2, 5.0, 440, 565]], []);
      return { cursor: { x: p.x, y: p.y, press: false, hide: t < 2.6 || t > 7.2 } };
    },
  };

  /* ---------- step 4: staged run and the prod gate ---------- */
  const NODES = [['resolve', 0.4, 1.0], ['dev', 1.1, 2.0], ['uat', 2.1, 3.0], ['prod-plan', 3.1, 4.0], ['prod', 6.5, null]];
  const PANEL_IN = 4.5, APPROVE_AT = 6.35;
  const step4 = {
    id: 'gate', name: 'Prod gate', dur: 8,
    html: gh(`
      <div class="ay-run">
        <h3 class="ay-title">iac-staged <span class="ay-num">#4182</span></h3>
        <div class="ay-sub">Triggered by a push to <code>main</code> · merged by <b>maria</b></div>
        <div class="ay-graph">${NODES.map(([n], i) => `${i ? '<i class="ay-edge"></i>' : ''}<div class="ay-node" data-i="${i}"><span class="ay-st"></span><span class="ay-nn">${esc(n)}</span><span class="ay-ns"></span></div>`).join('')}</div>
        <div class="ay-card ay-panel" id="panel">
          <div class="ay-chead"><b>Review pending deployments</b></div>
          <div class="ay-pbody">
            <div class="ay-env"><span class="ay-envdot"></span><b>prod</b> <span class="ay-dim2">Waiting for review from a required reviewer</span></div>
            <div class="ay-rev"><span class="ay-av">J</span><span><b>jonas</b> <span class="ay-dim2">required reviewer, not the author of this change</span></span></div>
            <div class="ay-btns"><span class="ay-btn">Reject</span><span class="ay-btn ay-go" id="appr">Approve and deploy</span></div>
          </div></div>
      </div>`, 'acme / platform / actions'),
    draw(t, $) {
      NODES.forEach(([, a, b], i) => {
        const n = $(`.ay-node[data-i="${i}"]`);
        const waiting = i === 4 && t >= 4.1 && t < APPROVE_AT + 0.25;
        const done = b != null && t >= b;
        const run = (b != null && t >= a && !done) || (i === 4 && t >= APPROVE_AT + 0.25);
        n.querySelector('.ay-st').innerHTML = done ? check('#1a7f37') : run ? `<i class="ay-spin" style="transform:rotate(${(t * 420) % 360}deg)"></i>` : waiting ? '<i class="ay-clock"></i>' : '<i class="ay-dot"></i>';
        n.querySelector('.ay-ns').textContent = done ? '' : waiting ? 'waiting' : run ? 'running' : '';
        n.classList.toggle('wait', waiting);
        n.style.opacity = t >= a - 0.3 || (i === 4 && t >= 3.4) ? 1 : 0.4;
      });
      const k = ease(t, PANEL_IN, PANEL_IN + 0.5);
      const p = $('#panel');
      p.style.opacity = k * (1 - ease(t, APPROVE_AT + 0.2, APPROVE_AT + 0.6));
      p.style.transform = `translateY(${(1 - k) * 30}px)`;
      $('#appr').classList.toggle('press', t >= APPROVE_AT && t < APPROVE_AT + 0.18);
      const g = glide(t, [[0, 0, 1000, 650], [5.0, 5.9, 745, 470]], [APPROVE_AT]);
      return { cursor: { x: g.x, y: g.y, press: g.press, hide: t < 5.0 || t > APPROVE_AT + 0.8 } };
    },
  };

  /* ---------- step 5: the ledger ---------- */
  const SHA = '7f3a9c1e5d20b8a4c6e1f09d2b3a47c5e8d61f0a';
  const JSON_LINES = [
    '{', '  "approver": "maria",', '  "breakglass_incident": null,', '  "converged": true,', '  "env": "prod",',
    '  "recorded_at": "2026-11-14T22:32:57.285826Z",', '  "repo": "acme/platform",', '  "run_attempt": 1,', '  "run_id": 4182,',
    `  "sha": "${SHA}",`, '  "units": {', '    "grants": {', '      "converged": true,', '      "status": "applied"', '    },',
    '    "workspace": {', '      "converged": true,', '      "status": "applied"', '    }', '  }', '}'];
  const J_AT = 2.5, J_STEP = 0.1;
  const step5 = {
    id: 'ledger', name: 'Ledger', dur: 7,
    html: term(
      cmd('c5', 'ayasee ledger show --store "$AYASEE_LEDGER_URL" --repo acme/platform --env prod', 0.4, 2.1) +
      JSON_LINES.map((l, i) => {
        const key = /"(approver|sha|converged)"/.exec(l);
        const cls = i === 1 || i === 9 ? ' ay-key' : '';
        return `<div class="ay-ln${cls}" data-at="${J_AT + i * J_STEP}" id="j${i}">${esc(l)}</div>`;
      }).join(''),
      'acme/platform: zsh'),
    draw(t, $) {
      drawTerm(t, $);
      const on = t >= 5.0;
      $('#j1').classList.toggle('on', on);
      $('#j9').classList.toggle('on', on);
      return { cursor: { x: 1000, y: 600, hide: true } };
    },
  };

  window.AYASEE_STEPS = [step1, step2, step3, step4, step5];
  window.AYASEE_CAPTIONS = [
    ['Scaffold one pinned profile', 'ayasee init writes the config, the callers and the bootstrap, all pinned to one release commit. Nothing to search and replace.'],
    ['Know what the first run needs', 'ayasee doctor lists everything the first run is missing in one pass, read-only, instead of failed jobs found one at a time.'],
    ['See the change before it ships', 'The pull request gets a read-only plan and the risk tiers. Nothing is applied. Here: analysts lose SELECT.'],
    ['Prod waits for someone else', 'dev and uat apply on merge. Prod stops at a gate that the author of the change cannot pass.'],
    ['The ledger remembers the exact SHA', 'The approved plan was applied, and the ledger records the SHA, the approver and whether it converged.'],
  ];
})();

(function () {
  const steps = window.AYASEE_STEPS, caps = window.AYASEE_CAPTIONS;
  const demo = HTMLDemo.mount(document.getElementById('demo'), { steps, tabs: ['acme/platform'], label: 'ayasee, step by step' });
  const cap = document.getElementById('cap'), scrub = document.getElementById('scrub'), pp = document.getElementById('pp'), tm = document.getElementById('time');
  const fmt = (s) => Math.floor(s / 60) + ':' + String(Math.floor(s % 60)).padStart(2, '0');
  let lastI = -1, dragging = false;
  scrub.max = demo.state().total;
  const sync = (st) => {
    if (st.i !== lastI) { lastI = st.i; cap.querySelector('h2').textContent = caps[st.i][0]; cap.querySelector('p').textContent = caps[st.i][1]; }
    if (!dragging) scrub.value = st.elapsed;
    tm.textContent = fmt(st.elapsed) + ' / ' + fmt(st.total);
  };
  demo.onChange(sync);
  sync(demo.state()); // under reduced motion nothing ticks, so fill the caption now
  scrub.addEventListener('pointerdown', () => { dragging = true; demo.pause(); });
  scrub.addEventListener('input', () => demo.seek(+scrub.value));
  scrub.addEventListener('change', () => { dragging = false; if (pp.getAttribute('aria-pressed') !== 'true') demo.resume(); });
  pp.addEventListener('click', () => {
    const paused = pp.getAttribute('aria-pressed') === 'true';
    paused ? demo.resume() : demo.pause();
    pp.setAttribute('aria-pressed', String(!paused)); pp.textContent = paused ? 'Pause' : 'Play';
  });
  const m = /[#&]t=([\d.]+)/.exec(location.hash); // #t=12.5 freezes the demo there (for shooting frames)
  if (m) { demo.pause(); demo.seek(+m[1]); pp.setAttribute('aria-pressed', 'true'); pp.textContent = 'Play'; }
  window.__demo = demo;
})();
