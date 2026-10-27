// RetroStick - Player1AI Design Edition Client Logic
let allGames = [];
let activeSystem = 'all';
let publicTunnelUrl = '';

document.addEventListener('DOMContentLoaded', () => {
  initSearchShortcut();
  loadSystemStatus();
  loadGames();
  setInterval(loadSystemStatus, 4000);
});

// Keyboard shortcut '/' to search (Exact behavior as player1ai.com)
function initSearchShortcut() {
  const searchInput = document.getElementById('searchInput');
  if (searchInput) {
    searchInput.addEventListener('input', () => {
      renderGames();
    });

    window.addEventListener('keydown', (e) => {
      if (e.key === '/' && document.activeElement !== searchInput) {
        e.preventDefault();
        searchInput.focus();
      }
    });
  }
}

// System Status Polling
async function loadSystemStatus() {
  try {
    const res = await fetch('/api/status');
    if (!res.ok) return;
    const stats = await res.json();

    const statCpu = document.getElementById('statCpu');
    const boxCpu = document.getElementById('boxCpu');
    if (statCpu) statCpu.textContent = stats.cpu_temp;
    if (boxCpu) boxCpu.textContent = stats.cpu_temp;

    const boxRam = document.getElementById('boxRam');
    if (boxRam) {
      boxRam.textContent = `${stats.mem_used_mb} / ${stats.mem_total_mb} MB (${stats.mem_percent}%)`;
    }

    // Cloud GPU & Queue Status
    try {
      const cRes = await fetch('/api/cloud/status');
      if (cRes.ok) {
        const cData = await cRes.json();
        const statQueuePill = document.getElementById('statQueuePill');
        if (statQueuePill) {
          const activeCount = cData.active_count || 0;
          const maxSlots = cData.max_slots || 2;
          const qLen = (cData.queue && cData.queue.queue_len) || 0;
          if (activeCount > 0) {
            statQueuePill.innerHTML = `🟡 <b>${activeCount}/${maxSlots}人</b> 激战中 ${qLen > 0 ? `(${qLen}人排队)` : ''}`;
          } else {
            statQueuePill.innerHTML = `🟢 <b>显卡空闲 (${maxSlots}空位)</b> · 随时开战`;
          }
        }
      }
    } catch (e) {}

    // Try reading tunnel URL
    if (!publicTunnelUrl) {
      try {
        const tRes = await fetch('/tunnel_url.txt');
        if (tRes.ok) {
          const tText = await tRes.text();
          const match = tText.match(/https:\/\/[a-zA-Z0-9-]+\.trycloudflare\.com/);
          if (match) publicTunnelUrl = match[0];
        }
      } catch (e) {}
    }
  } catch (e) {}
}

// Copy public share URL
function copyShareUrl() {
  const target = publicTunnelUrl || window.location.origin;
  navigator.clipboard.writeText(target).then(() => {
    const text = document.getElementById('shareBtnText');
    if (text) {
      text.textContent = '✅ 已复制专属链接！';
      setTimeout(() => text.textContent = '分享公网链接', 2500);
    }
  }).catch(() => {
    prompt('请复制以下专属公网链接：', target);
  });
}

// Load Game Library
async function loadGames() {
  const grid = document.getElementById('gamesGrid');
  try {
    const res = await fetch('/api/games');
    if (!res.ok) throw new Error('API Error');
    allGames = await res.json();
    
    const countAll = document.getElementById('countAll');
    const heroGameCount = document.getElementById('heroGameCount');
    if (countAll) countAll.textContent = allGames.length;
    if (heroGameCount) heroGameCount.textContent = allGames.length;

    renderGames();
    renderMobileSection();
  } catch (err) {
    if (grid) {
      grid.innerHTML = `
        <div class="empty-state">
          <p>⚠️ 无法加载游戏列表: ${err.message}</p>
          <button class="btn-accent" style="margin-top: 12px;" onclick="loadGames()">重试</button>
        </div>`;
    }
  }
}

// Filter System Tabs
function filterSystem(sys) {
  activeSystem = sys;
  const tabs = document.querySelectorAll('.filter-tab');
  tabs.forEach(tab => {
    if (tab.dataset.system === sys) {
      tab.classList.add('active');
    } else {
      tab.classList.remove('active');
    }
  });
  renderGames();
}

// Surprise Me (Player1AI signature random launch)
function surpriseMe() {
  if (!allGames.length) return;
  const rand = allGames[Math.floor(Math.random() * allGames.length)];
  const cloudUrl = `/cloud_player.html?rel_path=${encodeURIComponent(rand.rel_path)}&system=${encodeURIComponent(rand.system)}&title=${encodeURIComponent(rand.title)}`;
  window.location.href = cloudUrl;
}

// Helper: Escape HTML
function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// Generate Player1AI Card HTML
function createGameCardHtml(game) {
  const isGba = game.system === 'gba';
  const sysBadgeClass = isGba ? 'badge-gba' : (game.system === 'nes' ? 'badge-nes' : '');
  const cloudUrl = `/cloud_player.html?rel_path=${encodeURIComponent(game.rel_path)}&system=${encodeURIComponent(game.system)}&title=${encodeURIComponent(game.title)}`;
  const wasmUrl = `/emulator.html?system=${encodeURIComponent(game.system)}&core=${encodeURIComponent(game.core)}&game=${encodeURIComponent(game.rom_url)}&title=${encodeURIComponent(game.title)}&id=${encodeURIComponent(game.id)}`;

  return `
    <article class="card">
      <a href="${cloudUrl}" class="card-cover-link">
        <img class="card-cover-img" src="${game.cover}" alt="${escapeHtml(game.title)}" loading="lazy" onerror="this.src='/api/covers/${encodeURIComponent(game.filename)}.svg'">
        <span class="card-system-badge ${sysBadgeClass}">${game.system.toUpperCase()}</span>
      </a>

      <div class="card-body">
        <a href="${cloudUrl}" class="card-title" title="${escapeHtml(game.title)}">${escapeHtml(game.title)}</a>
        <p class="card-desc" title="${escapeHtml(game.desc || '')}">${escapeHtml(game.desc || '')}</p>

        <!-- Player1AI Style Chips -->
        <div class="card-chips">
          <span class="chip chip-accent">🚀 Adreno 306</span>
          <span class="chip">📱 触屏</span>
          <span class="chip">🎮 手柄</span>
          <span class="chip">60 FPS</span>
        </div>

        <div class="card-actions">
          <a href="${cloudUrl}" class="btn-cloud-play" title="随身 WiFi 物理显卡硬件加速云渲染">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
            云端渲染
          </a>
          <a href="${wasmUrl}" class="btn-wasm-play" title="客户端 WebAssembly 极速免流量游玩">
            ⚡ WASM
          </a>
        </div>
      </div>
    </article>
  `;
}

// Render Main Games Grid
function renderGames() {
  const grid = document.getElementById('gamesGrid');
  if (!grid) return;

  const searchInput = document.getElementById('searchInput');
  const searchVal = searchInput ? searchInput.value.trim().toLowerCase() : '';

  const filtered = allGames.filter(g => {
    const matchSys = activeSystem === 'all' || g.system === activeSystem;
    const matchSearch = !searchVal || g.title.toLowerCase().includes(searchVal) || g.filename.toLowerCase().includes(searchVal);
    return matchSys && matchSearch;
  });

  if (filtered.length === 0) {
    grid.innerHTML = `
      <div class="empty-state">
        <div style="font-size: 40px; margin-bottom: 8px;">🕹️</div>
        <p>未找到匹配的游戏</p>
        <span style="font-size: 13px; color: var(--muted);">请尝试搜索其他游戏名称，或切换分类标签</span>
      </div>`;
    return;
  }

  grid.innerHTML = filtered.map(createGameCardHtml).join('');
}

// Render Mobile Friendly Spotlight (Top 4 mobile-ready games)
function renderMobileSection() {
  const mGrid = document.getElementById('mobileGamesGrid');
  if (!mGrid || !allGames.length) return;

  // Pick mobile favorites: Pokemon, Mario, Zelda, Kirby
  const mobilePicks = allGames.filter(g => 
    g.id.includes('pokemon') || 
    g.id.includes('mario') || 
    g.id.includes('zelda') || 
    g.id.includes('kirby')
  ).slice(0, 4);

  mGrid.innerHTML = mobilePicks.map(createGameCardHtml).join('');
}
