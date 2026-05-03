export interface AudioAnalyzer {
  getData: () => Uint8Array
  stop: () => void
}

export async function createMicAnalyzer(): Promise<AudioAnalyzer> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
  const ctx = new AudioContext()
  const source = ctx.createMediaStreamSource(stream)
  const analyser = ctx.createAnalyser()
  analyser.fftSize = 256

  source.connect(analyser)
  const data = new Uint8Array(analyser.frequencyBinCount)

  return {
    getData: () => {
      analyser.getByteFrequencyData(data)
      return data
    },
    stop: () => {
      stream.getTracks().forEach(t => t.stop())
      ctx.close()
    },
  }
}

// 无麦克风时返回静音数据（用于待机）
export function createSilentAnalyzer(): AudioAnalyzer {
  const data = new Uint8Array(128)
  return {
    getData: () => data,
    stop: () => {},
  }
}
