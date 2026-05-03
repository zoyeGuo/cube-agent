const BASE_URL = 'http://localhost:8000/v1'
export const SESSION_KEY = 'secretary_session_id'

let sessionId: string | null = localStorage.getItem(SESSION_KEY)

export interface ChoiceItem { id: string; label: string; tag: string; recommended: boolean }
export interface SessionSummary {
  id: string
  created_at: string
  updated_at: string
  summary: string
  preview: string
  title: string
  message_count: number
}
export interface SessionMessage {
  role: 'user' | 'assistant'
  content: string
  ts: string
}
export interface SessionDetail {
  id: string
  created_at: string
  updated_at: string
  summary: string
  messages: SessionMessage[]
  last_assistant: string
}

export type ChatEvent =
  | { type: 'session'; session_id: string; request_id?: string | null }
  | { type: 'state'; name: string; scope: string }
  | { type: 'speech'; text: string; chunk: boolean }
  | { type: 'audio'; data: string; format: string }
  | { type: 'choice'; choice_id: string; title: string; items: ChoiceItem[]; extra_items: ChoiceItem[]; current_id: string | null }
  | { type: 'clarification'; question: string }
  | { type: 'schedule'; reminders: Array<{ id: string; message: string; run_time: string | null }> }
  | { type: 'done'; request_id: string }
  | { type: 'error'; code: string; message: string; request_id: string }

export async function sendMessage(
  message: string,
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE_URL}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, message }),
    signal,
  })

  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })

    const lines = buf.split('\n')
    buf = lines.pop() ?? ''

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      try {
        const event: ChatEvent = JSON.parse(line.slice(6))
        if (event.type === 'session') {
          sessionId = event.session_id
          localStorage.setItem(SESSION_KEY, sessionId)
        }
        onEvent(event)
      } catch {}
    }
  }
}

export function clearSession(): void {
  sessionId = null
  localStorage.removeItem(SESSION_KEY)
}

export function getSessionId(): string | null {
  return sessionId
}

export function setSessionId(nextSessionId: string | null): void {
  sessionId = nextSessionId
  if (nextSessionId) {
    localStorage.setItem(SESSION_KEY, nextSessionId)
  } else {
    localStorage.removeItem(SESSION_KEY)
  }
}

export async function listSessions(limit = 20): Promise<SessionSummary[]> {
  const res = await fetch(`${BASE_URL}/sessions?limit=${limit}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const data = await res.json() as { items?: SessionSummary[] }
  return data.items ?? []
}

export async function loadSession(sessionIdToLoad: string, limit = 12): Promise<SessionDetail> {
  const res = await fetch(`${BASE_URL}/sessions/${sessionIdToLoad}?limit=${limit}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return await res.json() as SessionDetail
}

export async function submitClarification(answer: string): Promise<void> {
  if (!sessionId) return
  await fetch(`${BASE_URL}/clarify`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, answer }),
  })
}

export async function submitChoice(choiceId: string, selectedId: string, selectedLabel: string): Promise<boolean> {
  const res = await fetch(`${BASE_URL}/choice`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      choice_id: choiceId,
      selected_id: selectedId,
      selected_label: selectedLabel,
      session_id: sessionId,
    }),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const data = await res.json() as { delivered?: boolean }
  return data.delivered !== false
}

export async function cancelRequest(requestId?: string | null, sessionIdToCancel?: string | null): Promise<void> {
  await fetch(`${BASE_URL}/cancel`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      request_id: requestId ?? null,
      session_id: sessionIdToCancel ?? sessionId,
    }),
  })
}
