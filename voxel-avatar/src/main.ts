import { getCurrentWindow } from '@tauri-apps/api/window'
import { createScene } from './voxel/scene'
import { createVoxelGrid } from './voxel/grid'
import { updateVoxels, setAudioActive, type MouseState } from './voxel/animator'
import { createSilentAnalyzer, type AudioAnalyzer } from './audio/analyzer'
import { playAudioBase64 } from './audio/player'
import {
  sendMessage,
  submitChoice,
  submitClarification,
  cancelRequest,
  clearSession,
  getSessionId,
  setSessionId,
  listSessions,
  loadSession,
  type SessionSummary,
  type SessionDetail,
} from './api/chat'

const canvas = document.getElementById('canvas') as HTMLCanvasElement
const { renderer, scene, camera, coreLight, coreMesh } = createScene(canvas)
const grid = createVoxelGrid(scene)

let analyzer: AudioAnalyzer = createSilentAnalyzer()
const mouse: MouseState = { x: 0, y: 0 }

const inputEl  = document.getElementById('input')    as HTMLInputElement
const historyBtn = document.getElementById('history') as HTMLButtonElement
const sendBtn  = document.getElementById('send')     as HTMLButtonElement
const stopBtn  = document.getElementById('stop')     as HTMLButtonElement
const resetBtn = document.getElementById('reset')    as HTMLButtonElement
const responseEl = document.getElementById('response') as HTMLDivElement
const statusEl = document.getElementById('status')   as HTMLDivElement

function setStatus(text: string) { statusEl.textContent = text }

function flashStatus(text: string, durationMs = 2000) {
  setStatus(text)
  window.setTimeout(() => {
    if (!requestWatchActive && statusEl.textContent === text) {
      setStatus('')
    }
  }, durationMs)
}

const STATUS_LABELS: Record<string, string> = {
  thinking: '思考中',
  calling_model: '调用模型中',
  tool_calling: '执行操作中',
  replanning: '重新规划中',
  session_compacting: '整理对话记忆中',
  context_high: '整理上下文中',
  context_critical: '压缩上下文中',
  waiting_user: '等待你的补充',
  speaking: '准备播报中',
  generating_audio: '生成语音中',
}

let requestWatchTimer: number | null = null
let requestStartAt = 0
let requestLastProgressAt = 0
let requestPhase = 'thinking'
let requestWatchActive = false
let activeRequestController: AbortController | null = null
let activeRequestId: string | null = null
let activeRunNonce = 0
const cancelledRuns = new Set<number>()
let currentSpeechPreview = ''
let audioPlaying = false
let inputComposing = false

function updateStopButton() {
  stopBtn.disabled = !(activeRequestController || audioPlaying)
}

function clearRequestWatch() {
  if (requestWatchTimer !== null) {
    window.clearInterval(requestWatchTimer)
    requestWatchTimer = null
  }
  requestWatchActive = false
}

function renderRequestStatus() {
  if (!requestWatchActive) return
  const elapsedSec = Math.max(1, Math.floor((Date.now() - requestStartAt) / 1000))
  const idleMs = Date.now() - requestLastProgressAt
  const base = STATUS_LABELS[requestPhase] ?? '处理中'

  if (requestPhase === 'waiting_user') {
    setStatus(base)
    return
  }
  if (idleMs >= 30000) {
    setStatus(`${base} ${elapsedSec}s，可能卡住了`)
    return
  }
  if (idleMs >= 12000) {
    setStatus(`${base} ${elapsedSec}s，仍在处理中`)
    return
  }
  if (elapsedSec >= 4) {
    setStatus(`${base} ${elapsedSec}s`)
    return
  }
  setStatus(`${base}...`)
}

function startRequestWatch(initialPhase = 'thinking') {
  clearRequestWatch()
  requestWatchActive = true
  requestPhase = initialPhase
  requestStartAt = Date.now()
  requestLastProgressAt = requestStartAt
  renderRequestStatus()
  requestWatchTimer = window.setInterval(renderRequestStatus, 1000)
}

function noteRequestProgress(nextPhase?: string) {
  if (!requestWatchActive) return
  requestLastProgressAt = Date.now()
  if (nextPhase) requestPhase = nextPhase
  renderRequestStatus()
}

// ── Clarification mode ────────────────────────────────────────────────────────
let clarificationMode = false

function enterClarification(question: string) {
  clarificationMode = true
  clearRequestWatch()
  responseEl.textContent = question
  setStatus('WAITING...')
  inputEl.placeholder = '回答问题...'
  sendBtn.disabled = false
  inputEl.focus()
}

function exitClarification() {
  clarificationMode = false
  inputEl.placeholder = '输入消息...'
}

function setAudioPlaying(next: boolean) {
  audioPlaying = next
  setAudioActive(next)
  updateStopButton()
}

function stopPlayback() {
  analyzer.stop()
  analyzer = createSilentAnalyzer()
  setAudioPlaying(false)
}

async function interruptCurrentResponse(options: { silent?: boolean } = {}) {
  const hadRequest = Boolean(activeRequestController)
  const hadAudio = audioPlaying
  if (!hadRequest && !hadAudio) return

  const requestId = activeRequestId
  const sessionId = getSessionId()
  const runNonce = activeRunNonce

  if (hadRequest) {
    cancelledRuns.add(runNonce)
    const controller = activeRequestController
    activeRequestController = null
    activeRequestId = null
    updateStopButton()
    controller?.abort()
    try {
      await cancelRequest(requestId, sessionId)
    } catch {}
  }

  if (hadAudio) {
    stopPlayback()
    setStatus('')
  }

  clearRequestWatch()
  if (clarificationMode) exitClarification()
  sendBtn.disabled = false
  inputEl.focus()

  if (!options.silent) {
    flashStatus(hadRequest ? '已停止当前响应' : '已停止播报')
  }
}

async function submit() {
  const msg = inputEl.value.trim()
  if (!msg) return
  hideSessionPanel()

  if (activeRequestController || audioPlaying) {
    await interruptCurrentResponse({ silent: true })
  }

  if (clarificationMode) {
    exitClarification()
    inputEl.value = ''
    sendBtn.disabled = true
    startRequestWatch('thinking')
    await submitClarification(msg)
    sendBtn.disabled = false
    inputEl.focus()
    return
  }

  inputEl.value = ''
  sendBtn.disabled = true
  responseEl.textContent = ''
  currentSpeechPreview = ''
  startRequestWatch('thinking')
  const runNonce = ++activeRunNonce
  const controller = new AbortController()
  activeRequestController = controller
  activeRequestId = null
  updateStopButton()

  let finalText = ''   // speech(chunk=false) 的完整文字
  let pendingText = '' // chunks 累积，作为兜底
  let terminalEventSeen = false

  try {
    await sendMessage(msg, async (event) => {
      if (runNonce !== activeRunNonce) return
      if (event.type === 'session') {
        activeRequestId = event.request_id ?? activeRequestId
        updateStopButton()
        connectWs(event.session_id)
      } else if (event.type === 'choice') {
        showPanel(event as any)
      } else if (event.type === 'state') {
        if (event.name === 'waiting_user') {
          requestPhase = event.name
          renderRequestStatus()
        } else {
          noteRequestProgress(event.name)
        }
      } else if (event.type === 'speech') {
        if (event.chunk) {
          pendingText += event.text
        } else {
          finalText = event.text  // 完整文字，优先用这个
        }
        currentSpeechPreview = finalText || pendingText
      } else if (event.type === 'audio') {
        activeRequestController = null
        activeRequestId = null
        updateStopButton()
        clearRequestWatch()
        // 解码完成后才显示文字 + 触发动效，确保声音与动画同步
        stopPlayback()
        analyzer = await playAudioBase64(
          event.data,
          () => {
            // onStart：解码完、开始播放时触发
            responseEl.textContent = currentSpeechPreview || finalText || pendingText
            setStatus('SPEAKING...')
            setAudioPlaying(true)
          },
          () => {
            // onEnded：播放结束
            setAudioPlaying(false)
            setStatus('')
            analyzer = createSilentAnalyzer()
          },
        )
      } else if (event.type === 'schedule') {
        showSchedulePanel(event.reminders as any)
      } else if (event.type === 'clarification') {
        enterClarification(event.question)
      } else if (event.type === 'done') {
        terminalEventSeen = true
        activeRequestController = null
        activeRequestId = null
        updateStopButton()
        clearRequestWatch()
        // TTS 失败兜底：至少显示文字
        if (!responseEl.textContent) responseEl.textContent = currentSpeechPreview || finalText || pendingText
        setStatus('')
      } else if (event.type === 'error') {
        terminalEventSeen = true
        activeRequestController = null
        activeRequestId = null
        updateStopButton()
        clearRequestWatch()
        console.error('[secretary error]', event.code, event.message)
        setStatus('处理失败，请重试')
        if (!responseEl.textContent) responseEl.textContent = event.message
      }
    }, controller.signal)
    if (runNonce === activeRunNonce) {
      activeRequestController = null
      activeRequestId = null
      updateStopButton()
    }
    if (!terminalEventSeen && !clarificationMode && !cancelledRuns.has(runNonce)) {
      clearRequestWatch()
      setStatus('本次响应中断了，请重试')
    }
  } catch (error) {
    const wasCancelled = cancelledRuns.has(runNonce)
    if (runNonce === activeRunNonce) {
      activeRequestController = null
      activeRequestId = null
      updateStopButton()
    }
    if (wasCancelled) {
      clearRequestWatch()
      if (!responseEl.textContent && currentSpeechPreview) {
        responseEl.textContent = currentSpeechPreview
      }
      setStatus('')
    } else {
      console.error('[secretary abort?]', error)
      clearRequestWatch()
      setStatus('连接失败，请确认后端已启动')
    }
  } finally {
    cancelledRuns.delete(runNonce)
    if (runNonce === activeRunNonce) {
      sendBtn.disabled = false
      inputEl.focus()
      updateStopButton()
    }
  }
}

// ── Context Panel ─────────────────────────────────────────────────────────────
const panel       = document.getElementById('context-panel')!
const panelTitle  = document.getElementById('panel-title')!
const panelItems  = document.getElementById('panel-items')!
const panelExtra  = document.getElementById('panel-extra')!
const panelExpand = document.getElementById('panel-expand') as HTMLButtonElement
const panelDivider = document.getElementById('panel-divider') as HTMLHRElement
const panelClose  = document.getElementById('panel-close') as HTMLButtonElement

let currentChoiceId = ''
let panelExpanded = false

function showPanel(event: {
  choice_id: string; title: string;
  items: Array<{id: string; label: string; tag: string; recommended: boolean}>;
  extra_items: Array<{id: string; label: string; tag: string; recommended: boolean}>;
  current_id?: string | null;
}) {
  hideSessionPanel()
  currentChoiceId = event.choice_id
  panelTitle.textContent = event.title.toUpperCase()
  panelExpanded = false

  const renderItem = (item: typeof event.items[0], container: HTMLElement) => {
    const isCurrent = event.current_id != null && item.id === event.current_id
    const div = document.createElement('div')
    div.className = 'panel-item'
      + (isCurrent ? ' current' : '')
      + (!isCurrent && item.recommended ? ' recommended' : '')
    div.dataset.id = item.id
    div.dataset.label = item.label
    div.innerHTML = `
      <span class="item-label">${item.label}</span>
      ${isCurrent
        ? '<span class="item-current-dot"></span>'
        : item.recommended
          ? '<span class="item-tag rec-tag">推荐</span>'
          : item.tag ? `<span class="item-tag">${item.tag}</span>` : ''}
    `
    div.addEventListener('click', () => onItemSelect(item.id, item.label))
    container.appendChild(div)
  }

  panelItems.innerHTML = ''
  panelExtra.innerHTML = ''
  panelExtra.className = ''
  event.items.forEach(item => renderItem(item, panelItems))

  if (event.extra_items?.length) {
    event.extra_items.forEach(item => renderItem(item, panelExtra))
    panelDivider.style.display = 'block'
    panelExpand.style.display = 'block'
    panelExpand.textContent = '展开更多 ▼'
  } else {
    panelDivider.style.display = 'none'
    panelExpand.style.display = 'none'
  }

  panel.classList.add('visible')
  canvas.classList.add('panel-open')
}

function hidePanel() {
  panel.classList.remove('visible')
  canvas.classList.remove('panel-open')
}

async function onItemSelect(id: string, label: string) {
  hidePanel()
  const delivered = await submitChoice(currentChoiceId, id, label)
  if (!delivered) {
    flashStatus('这次确认已失效，请重试', 2600)
  }
}

panelExpand.addEventListener('click', () => {
  panelExpanded = !panelExpanded
  panelExtra.className = panelExpanded ? 'open' : ''
  panelExpand.textContent = panelExpanded ? '收起 ▲' : '展开更多 ▼'
})

panelClose.addEventListener('click', hidePanel)

// ── Schedule Panel ────────────────────────────────────────────────────────────
const schedulePanel = document.getElementById('schedule-panel')!
const scheduleItems = document.getElementById('schedule-items')!
const scheduleClose = document.getElementById('schedule-close') as HTMLButtonElement
const sessionPanel = document.getElementById('session-panel')!
const sessionItems = document.getElementById('session-items')!
const sessionClose = document.getElementById('session-close') as HTMLButtonElement
const sessionDetailTitle = document.getElementById('session-detail-title') as HTMLDivElement
const sessionDetailMeta = document.getElementById('session-detail-meta') as HTMLDivElement
const sessionDetailSummary = document.getElementById('session-detail-summary') as HTMLDivElement
const sessionDetailMessages = document.getElementById('session-detail-messages') as HTMLDivElement
const sessionRestoreBtn = document.getElementById('session-restore') as HTMLButtonElement

let sessionPanelEntries: SessionSummary[] = []
let sessionPanelSelectedId: string | null = null
let sessionPreviewRequestId = 0
const sessionDetailCache = new Map<string, SessionDetail>()

function formatRunTime(iso: string | null): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    const now = new Date()
    const isToday = d.toDateString() === now.toDateString()
    const hm = d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    if (isToday) return `今天 ${hm}`
    const mm = d.getMonth() + 1
    const dd = d.getDate()
    return `${mm}月${dd}日 ${hm}`
  } catch { return iso.slice(0, 16) }
}

function showSchedulePanel(reminders: Array<{ id: string; message: string; run_time: string | null }>) {
  hideSessionPanel()
  scheduleItems.innerHTML = ''
  if (!reminders.length) {
    scheduleItems.innerHTML = '<div class="schedule-empty">暂无安排</div>'
  } else {
    for (const r of reminders) {
      const div = document.createElement('div')
      div.className = 'schedule-item'
      div.innerHTML = `
        <div class="schedule-time">${formatRunTime(r.run_time)}</div>
        <div class="schedule-msg">${r.message}</div>
      `
      scheduleItems.appendChild(div)
    }
  }
  schedulePanel.classList.add('visible')
}

function hideSchedulePanel() {
  schedulePanel.classList.remove('visible')
}

scheduleClose.addEventListener('click', hideSchedulePanel)

function formatSessionTime(iso: string): string {
  try {
    const d = new Date(iso)
    const now = new Date()
    const isToday = d.toDateString() === now.toDateString()
    const hm = d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    if (isToday) return `今天 ${hm}`
    const month = d.getMonth() + 1
    const day = d.getDate()
    return `${month}月${day}日 ${hm}`
  } catch {
    return iso.slice(0, 16)
  }
}

function hideSessionPanel() {
  sessionPanel.classList.remove('visible')
  sessionPreviewRequestId += 1
}

function renderSessionDetailPlaceholder(
  title: string,
  summary: string,
  emptyMessage: string,
  meta = '',
) {
  sessionDetailTitle.textContent = title
  sessionDetailMeta.textContent = meta
  sessionDetailSummary.textContent = summary
  sessionDetailMessages.innerHTML = `<div class="session-detail-empty">${emptyMessage}</div>`
  sessionRestoreBtn.disabled = true
  sessionRestoreBtn.textContent = '切回这段会话'
}

function renderSessionDetailLoading(item?: SessionSummary) {
  const metaParts = []
  if (item?.updated_at) metaParts.push(formatSessionTime(item.updated_at))
  if (item?.message_count) metaParts.push(`${item.message_count} 条消息`)

  renderSessionDetailPlaceholder(
    item?.title || '会话预览',
    item?.summary || item?.preview || '正在载入这段会话...',
    '正在载入最近几轮消息...',
    metaParts.join(' · '),
  )
  sessionRestoreBtn.textContent = '读取中...'
}

function updateSessionRestoreButton() {
  if (!sessionPanelSelectedId) {
    sessionRestoreBtn.disabled = true
    sessionRestoreBtn.textContent = '切回这段会话'
    return
  }
  if (sessionPanelSelectedId === getSessionId()) {
    sessionRestoreBtn.disabled = true
    sessionRestoreBtn.textContent = '当前正在使用这段会话'
    return
  }
  sessionRestoreBtn.disabled = false
  sessionRestoreBtn.textContent = '切回这段会话'
}

function renderSessionDetail(detail: SessionDetail) {
  const item = sessionPanelEntries.find((entry) => entry.id === detail.id)
  const metaParts = [formatSessionTime(detail.updated_at)]
  if (item?.message_count) metaParts.push(`${item.message_count} 条消息`)
  if (detail.id === getSessionId()) metaParts.push('当前会话')

  sessionDetailTitle.textContent = item?.title || '这段会话'
  sessionDetailMeta.textContent = metaParts.join(' · ')
  sessionDetailSummary.textContent = detail.summary || item?.preview || '这段会话还没有摘要'
  sessionDetailMessages.innerHTML = ''

  const messages = detail.messages.slice(-6)
  if (!messages.length) {
    sessionDetailMessages.innerHTML = '<div class="session-detail-empty">这段会话里还没有可预览的消息</div>'
  } else {
    for (const message of messages) {
      const bubble = document.createElement('div')
      bubble.className = `session-detail-message ${message.role}`

      const role = document.createElement('div')
      role.className = 'session-detail-role'
      role.textContent = message.role === 'user' ? '你' : '助手'

      const content = document.createElement('div')
      content.className = 'session-detail-content'
      content.textContent = message.content

      bubble.append(role, content)
      sessionDetailMessages.appendChild(bubble)
    }
  }

  updateSessionRestoreButton()
}

function renderSessionList(items: SessionSummary[]) {
  sessionItems.innerHTML = ''
  if (!items.length) {
    sessionItems.innerHTML = '<div class="session-empty">还没有可恢复的会话</div>'
    renderSessionDetailPlaceholder(
      '会话预览',
      '这里会显示你之前聊过的内容摘要。',
      '先聊几句，系统就会在这里积累可恢复的会话。',
    )
    return
  }

  const activeSessionId = getSessionId()
  for (const item of items) {
    const button = document.createElement('button')
    button.type = 'button'
    const isCurrent = item.id === activeSessionId
    const isSelected = item.id === sessionPanelSelectedId
    button.className = 'session-item'
      + (isCurrent ? ' current' : '')
      + (isSelected ? ' selected' : '')
    const timeEl = document.createElement('div')
    timeEl.className = 'session-item-time'
    timeEl.textContent = formatSessionTime(item.updated_at)

    const titleEl = document.createElement('div')
    titleEl.className = 'session-item-title'
    titleEl.textContent = item.title

    const previewEl = document.createElement('div')
    previewEl.className = 'session-item-preview'
    previewEl.textContent = item.preview || '这段会话还没有摘要'

    button.append(timeEl, titleEl, previewEl)
    button.addEventListener('click', () => { void previewSession(item.id) })
    sessionItems.appendChild(button)
  }
}

async function previewSession(sessionIdToLoad: string) {
  sessionPanelSelectedId = sessionIdToLoad
  renderSessionList(sessionPanelEntries)

  const cached = sessionDetailCache.get(sessionIdToLoad)
  if (cached) {
    renderSessionDetail(cached)
    return
  }

  const item = sessionPanelEntries.find((entry) => entry.id === sessionIdToLoad)
  renderSessionDetailLoading(item)

  const requestId = ++sessionPreviewRequestId
  try {
    const detail = await loadSession(sessionIdToLoad, 12)
    sessionDetailCache.set(detail.id, detail)
    if (requestId !== sessionPreviewRequestId || sessionPanelSelectedId !== sessionIdToLoad) return
    renderSessionDetail(detail)
  } catch {
    if (requestId !== sessionPreviewRequestId || sessionPanelSelectedId !== sessionIdToLoad) return
    renderSessionDetailPlaceholder(
      item?.title || '会话预览',
      item?.summary || item?.preview || '这段会话摘要暂时不可用。',
      '这段会话预览加载失败，可以稍后再试。',
      item?.updated_at ? formatSessionTime(item.updated_at) : '',
    )
    updateSessionRestoreButton()
  }
}

async function openSessionPanel() {
  hideSchedulePanel()
  hidePanel()
  sessionDetailCache.clear()
  sessionPanelEntries = []
  sessionPanelSelectedId = null
  sessionItems.innerHTML = '<div class="session-empty">加载中...</div>'
  renderSessionDetailLoading()
  sessionPanel.classList.add('visible')
  try {
    const items = await listSessions(24)
    sessionPanelEntries = items
    renderSessionList(items)
    if (!items.length) return
    const activeSessionId = getSessionId()
    const initialSessionId =
      (activeSessionId && items.some((item) => item.id === activeSessionId))
        ? activeSessionId
        : items[0].id
    if (!initialSessionId) return
    await previewSession(initialSessionId)
  } catch {
    sessionItems.innerHTML = '<div class="session-empty">会话列表加载失败</div>'
    renderSessionDetailPlaceholder(
      '会话预览',
      '会话面板暂时没能加载出来。',
      '可以稍后再试，或者先继续当前对话。',
    )
  }
}

async function restoreSession(sessionIdToLoad: string) {
  sessionRestoreBtn.disabled = true
  sessionRestoreBtn.textContent = '切回中...'
  try {
    const detail = sessionDetailCache.get(sessionIdToLoad) ?? await loadSession(sessionIdToLoad, 16)
    sessionDetailCache.set(detail.id, detail)
    clearRequestWatch()
    exitClarification()
    stopPlayback()
    setSessionId(detail.id)
    connectWs(detail.id)
    hideSessionPanel()
    hideSchedulePanel()
    hidePanel()
    responseEl.textContent = detail.last_assistant || detail.summary || '已切回之前的会话'
    sendBtn.disabled = false
    flashStatus('已切回之前的会话')
    inputEl.focus()
  } catch {
    flashStatus('恢复会话失败', 2600)
    updateSessionRestoreButton()
  }
}

sessionClose.addEventListener('click', hideSessionPanel)
sessionRestoreBtn.addEventListener('click', () => {
  if (!sessionPanelSelectedId) return
  void restoreSession(sessionPanelSelectedId)
})

async function resetConversation() {
  await interruptCurrentResponse({ silent: true })
  clearRequestWatch()
  exitClarification()
  clearSession()
  disconnectWs()
  hidePanel()
  hideSchedulePanel()
  hideSessionPanel()
  stopPlayback()
  inputEl.value = ''
  responseEl.textContent = ''
  currentSpeechPreview = ''
  sendBtn.disabled = false
  flashStatus('已开始新对话')
  inputEl.focus()
}

sendBtn.addEventListener('click', submit)
stopBtn.addEventListener('click', () => { void interruptCurrentResponse() })
historyBtn.addEventListener('click', () => {
  if (sessionPanel.classList.contains('visible')) {
    hideSessionPanel()
    return
  }
  void openSessionPanel()
})
resetBtn.addEventListener('click', () => { void resetConversation() })
inputEl.addEventListener('compositionstart', () => {
  inputComposing = true
})
inputEl.addEventListener('compositionend', () => {
  inputComposing = false
})
inputEl.addEventListener('keydown', (e) => {
  if (e.key !== 'Enter') return
  if (e.isComposing || inputComposing || (e as KeyboardEvent).keyCode === 229) {
    return
  }
  e.preventDefault()
  void submit()
})

// 拖拽区域
const dragEl = document.getElementById('drag-region')
dragEl?.addEventListener('mousedown', () => {
  getCurrentWindow().startDragging().catch(() => {})
})

window.addEventListener('mousemove', (e) => {
  mouse.x = (e.clientX / window.innerWidth) * 2 - 1
  mouse.y = (e.clientY / window.innerHeight) * 2 - 1
})

window.addEventListener('wheel', (e) => {
  if (panel.contains(e.target as Node)) return
  camera.position.z = Math.max(30, Math.min(100, camera.position.z + e.deltaY * 0.08))
})

// ── WebSocket 主动推送 ─────────────────────────────────────────────────────
let proactiveWs: WebSocket | null = null
let proactiveWsSessionId: string | null = null
let proactiveWsPingTimer: number | null = null
let proactiveWsReconnectTimer: number | null = null

function clearWsTimers() {
  if (proactiveWsPingTimer !== null) {
    window.clearInterval(proactiveWsPingTimer)
    proactiveWsPingTimer = null
  }
  if (proactiveWsReconnectTimer !== null) {
    window.clearTimeout(proactiveWsReconnectTimer)
    proactiveWsReconnectTimer = null
  }
}

function disconnectWs() {
  clearWsTimers()
  proactiveWsSessionId = null
  if (proactiveWs) {
    proactiveWs.onclose = null
    proactiveWs.close()
    proactiveWs = null
  }
}

async function handleProactive(text: string, audioB64?: string) {
  responseEl.textContent = text
  if (audioB64) {
    setStatus('SPEAKING...')
    setAudioActive(true)
    stopPlayback()
    analyzer = await playAudioBase64(
      audioB64,
      () => { responseEl.textContent = text; setStatus('SPEAKING...'); setAudioActive(true) },
      () => { setAudioActive(false); setStatus('') },
    ).catch(() => { setAudioActive(false); setStatus(''); return createSilentAnalyzer() })
  } else {
    setStatus('REMINDER')
    setTimeout(() => { if (statusEl.textContent === 'REMINDER') setStatus('') }, 4000)
  }
}

function scheduleWsReconnect(sessionId: string) {
  if (proactiveWsReconnectTimer !== null) return
  proactiveWsReconnectTimer = window.setTimeout(() => {
    proactiveWsReconnectTimer = null
    connectWs(sessionId)
  }, 3000)
}

function connectWs(sessionId = getSessionId()) {
  if (!sessionId) return
  const sameSession =
    proactiveWsSessionId === sessionId &&
    proactiveWs &&
    (proactiveWs.readyState === WebSocket.OPEN || proactiveWs.readyState === WebSocket.CONNECTING)
  if (sameSession) return

  if (proactiveWs) {
    proactiveWs.onclose = null
    proactiveWs.close()
    proactiveWs = null
  }
  clearWsTimers()
  proactiveWsSessionId = sessionId

  const ws = new WebSocket(`ws://localhost:8000/v1/ws/${sessionId}`)
  proactiveWs = ws

  ws.onmessage = (e) => {
    try {
      const event = JSON.parse(e.data)
      if (event.type === 'proactive') {
        handleProactive(event.text, event.audio)
      }
    } catch {}
  }

  ws.onopen = () => {
    clearWsTimers()
    proactiveWsPingTimer = window.setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send('ping')
      } else if (proactiveWsPingTimer !== null) {
        window.clearInterval(proactiveWsPingTimer)
        proactiveWsPingTimer = null
      }
    }, 25000)
  }

  ws.onerror = () => ws.close()

  ws.onclose = () => {
    if (proactiveWs !== ws) return
    proactiveWs = null
    if (proactiveWsPingTimer !== null) {
      window.clearInterval(proactiveWsPingTimer)
      proactiveWsPingTimer = null
    }
    const activeSessionId = getSessionId()
    if (activeSessionId && activeSessionId === sessionId) {
      scheduleWsReconnect(sessionId)
    } else {
      proactiveWsSessionId = null
    }
  }
}

connectWs(getSessionId())
updateStopButton()

function loop(time: number) {
  requestAnimationFrame(loop)
  updateVoxels(grid, analyzer.getData(), time * 0.001, mouse, coreLight, coreMesh)
  renderer.render(scene, camera)
}

requestAnimationFrame(loop)
