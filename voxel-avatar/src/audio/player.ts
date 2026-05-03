import type { AudioAnalyzer } from './analyzer'

export async function playAudioBase64(
  b64: string,
  onStart: () => void,
  onEnded: () => void,
): Promise<AudioAnalyzer> {
  const binary = atob(b64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)

  const ctx = new AudioContext()
  const buffer = await ctx.decodeAudioData(bytes.buffer.slice(0))

  const source = ctx.createBufferSource()
  const analyser = ctx.createAnalyser()
  analyser.fftSize = 256

  source.buffer = buffer
  source.connect(analyser)
  analyser.connect(ctx.destination)
  source.onended = onEnded
  onStart()   // 解码完成、真正开始播放前触发
  source.start()

  const data = new Uint8Array(analyser.frequencyBinCount)

  return {
    getData: () => {
      analyser.getByteFrequencyData(data)
      return data
    },
    stop: () => {
      try { source.stop() } catch {}
      ctx.close()
    },
  }
}
