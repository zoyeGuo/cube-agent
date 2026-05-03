import * as THREE from 'three'
import type { VoxelGrid } from './grid'

// 蓝灰渐变（静默）—— 暗部到 #a4c0d8 再到近白
const GRADIENT_BLUE = [
  new THREE.Color(0x0d1820),  // 极深钢蓝
  new THREE.Color(0x253c4e),  // 深灰蓝
  new THREE.Color(0x567a92),  // 中灰蓝
  new THREE.Color(0xa4c0d8),  // 目标色
  new THREE.Color(0xd4e5f0),  // 浅雾蓝
  new THREE.Color(0xeef5fa),  // 近白
]

// 粉色渐变（语音）—— 与 #a4c0d8 协调的玫瑰粉
const GRADIENT_PINK = [
  new THREE.Color(0x1f0d14),  // 极深玫瑰
  new THREE.Color(0x5c2a3a),  // 深玫瑰
  new THREE.Color(0xb05070),  // 中玫瑰
  new THREE.Color(0xe889a8),  // 浅粉（饱和度与 #a4c0d8 相近）
  new THREE.Color(0xf5c8d8),  // 雾粉
  new THREE.Color(0xfdf0f4),  // 近白
]

function sampleGradient(palette: THREE.Color[], t: number): THREE.Color {
  t = Math.max(0, Math.min(1, t))
  const scaled = t * (palette.length - 1)
  const i = Math.floor(scaled)
  const f = scaled - i
  return new THREE.Color().lerpColors(
    palette[Math.min(i, palette.length - 1)],
    palette[Math.min(i + 1, palette.length - 1)],
    f,
  )
}

function gradientColor(t: number, colorBlend: number): THREE.Color {
  const blue = sampleGradient(GRADIENT_BLUE, t)
  const pink = sampleGradient(GRADIENT_PINK, t)
  return new THREE.Color().lerpColors(blue, pink, colorBlend)
}

function easeInOut(t: number): number {
  t = Math.max(0, Math.min(1, t))
  return t * t * (3 - 2 * t)
}

export interface MouseState { x: number; y: number }

// ── 8字形路径参数 ─────────────────────────────────────────────────────────
const FIGURE8_R = 15  // 每个圆环半径
const MOBIUS_W = 2.8  // 带宽
const U_COUNT = 72
const V_COUNT = 3     // 72×3=216=6³

const blockV: number[] = []
for (let ui = 0; ui < U_COUNT; ui++)
  for (let vi = 0; vi < V_COUNT; vi++)
    blockV.push((vi / (V_COUNT - 1) - 0.5) * 2 * MOBIUS_W)

const blockUBase: number[] = []
for (let ui = 0; ui < U_COUNT; ui++)
  for (let vi = 0; vi < V_COUNT; vi++)
    blockUBase.push((ui / U_COUNT) * Math.PI * 2)

function getMobiusPos(u: number, v: number): THREE.Vector3 {
  // 8字中心线：交叉点在原点（核子位置）
  const cx = FIGURE8_R * Math.sin(u)
  const cy = FIGURE8_R * Math.sin(u) * Math.cos(u) * 1.6  // 拉高让两个圆更圆

  // 切线方向（求导）
  const tx = FIGURE8_R * Math.cos(u)
  const ty = FIGURE8_R * Math.cos(2 * u) * 1.6
  const tLen = Math.sqrt(tx * tx + ty * ty) || 0.001

  // 法线（切线旋转90°）
  const nx = -ty / tLen
  const ny =  tx / tLen

  // 莫比乌斯半扭转：u 从 0→2π，带面翻转180°
  const twist = u / 2
  const cosT = Math.cos(twist)
  const sinT = Math.sin(twist)

  return new THREE.Vector3(
    cx + v * cosT * nx,
    cy + v * cosT * ny,
    v * sinT * 0.45,   // Z轴扭转，保持扁平
  )
}

// ── 全局状态 ──────────────────────────────────────────────────────────────
let smoothBass = 0
let smoothFreqs: Float32Array | null = null
let audioActivityTarget = 0
let audioActivity = 0
let colorBlend = 0      // 0=蓝 1=粉，平滑过渡
let flowOffset = 0

export function setAudioActive(active: boolean) {
  audioActivityTarget = active ? 1 : 0
}

export function updateVoxels(
  grid: VoxelGrid,
  audioData: Uint8Array,
  time: number,
  _mouse: MouseState,
  coreLight: THREE.PointLight,
  coreMesh: THREE.Mesh,
) {
  const { meshes, origins, group } = grid
  const len = audioData.length

  if (!smoothFreqs || smoothFreqs.length !== meshes.length)
    smoothFreqs = new Float32Array(meshes.length)

  // 低频能量
  let rawBass = 0
  for (let i = 0; i < 8; i++) rawBass += audioData[i]
  rawBass = rawBass / (8 * 255)
  smoothBass += rawBass > smoothBass
    ? (rawBass - smoothBass) * 0.12
    : (rawBass - smoothBass) * 0.04
  const bassEnergy = smoothBass

  // audioActivity：快升慢降
  audioActivity += audioActivityTarget > audioActivity
    ? (audioActivityTarget - audioActivity) * 0.12
    : (audioActivityTarget - audioActivity) * 0.022

  // colorBlend：语音时渐变为粉，结束后缓慢归蓝（比形态恢复更慢，有拖尾感）
  const colorTarget = audioActivityTarget
  colorBlend += colorTarget > colorBlend
    ? (colorTarget - colorBlend) * 0.06
    : (colorTarget - colorBlend) * 0.012

  // 流动速度
  if (audioActivity > 0.01)
    flowOffset += (0.014 + bassEnergy * 0.025) * audioActivity

  // ── 组旋转 ────────────────────────────────────────────────────────────
  if (audioActivity < 0.05) {
    group.rotation.y += 0.003
    group.rotation.x += (0 - group.rotation.x) * 0.02
  } else {
    group.rotation.y += (0 - group.rotation.y) * 0.04
    group.rotation.x += (0 - group.rotation.x) * 0.04
  }

  // ── 核心光球颜色也跟着过渡 ───────────────────────────────────────────
  const breathSpeed = 1.5 + bassEnergy * 3
  const breath = Math.sin(time * breathSpeed) * 0.5 + 0.5
  const breathAmp = 0.35 + bassEnergy * 0.5

  const coreIdleColor = new THREE.Color().lerpColors(new THREE.Color(0x253c4e), new THREE.Color(0xd4e5f0), breath * 0.7)
  const coreActiveColor = new THREE.Color().lerpColors(new THREE.Color(0x5c2a3a), new THREE.Color(0xf5c8d8), breath * 0.7)
  const coreFinalColor = new THREE.Color().lerpColors(coreIdleColor, coreActiveColor, colorBlend)

  coreMesh.scale.setScalar(0.7 + breath * 0.8 + bassEnergy * 0.6)
  coreLight.intensity = 4 + breath * breathAmp * 14 + bassEnergy * 20
  ;(coreMesh.material as THREE.MeshBasicMaterial).color.copy(coreFinalColor)

  // 点光源颜色也跟随
  coreLight.color.lerpColors(new THREE.Color(0x567a92), new THREE.Color(0xb05070), colorBlend)

  const morphT = easeInOut(audioActivity)
  const maxDist = origins.reduce((m, o) => Math.max(m, o.length()), 0)

  meshes.forEach((mesh, i) => {
    const freqIndex = Math.floor((i / meshes.length) * len)
    const rawFreq = audioData[freqIndex] / 255
    smoothFreqs![i] += rawFreq > smoothFreqs![i]
      ? (rawFreq - smoothFreqs![i]) * 0.12
      : (rawFreq - smoothFreqs![i]) * 0.04
    const freqVal = smoothFreqs![i]

    const origin = origins[i]
    const dist = origin.length()
    const proximity = 1 - dist / maxDist

    // ── 位置 ──────────────────────────────────────────────────────────────
    const u = blockUBase[i] + flowOffset
    const v = blockV[i] + freqVal * 1.0 * audioActivity
    const mobiusPos = getMobiusPos(u, v)

    const targetX = origin.x + (mobiusPos.x - origin.x) * morphT
    const targetY = origin.y + (mobiusPos.y - origin.y) * morphT
    const targetZ = origin.z + (mobiusPos.z - origin.z) * morphT

    mesh.position.x += (targetX - mesh.position.x) * 0.1
    mesh.position.y += (targetY - mesh.position.y) * 0.1
    mesh.position.z += (targetZ - mesh.position.z) * 0.1

    // ── 朝向 ──────────────────────────────────────────────────────────────
    if (morphT > 0.05) {
      const nextPos = getMobiusPos(u + 0.01, v)
      const tangent = nextPos.clone().sub(mobiusPos).normalize()
      const up = new THREE.Vector3(0, 1, 0)
      const axis = up.clone().cross(tangent).normalize()
      const angle = Math.acos(Math.max(-1, Math.min(1, up.dot(tangent))))
      mesh.quaternion.slerp(new THREE.Quaternion().setFromAxisAngle(axis, angle), 0.08 * morphT)
    } else {
      mesh.quaternion.slerp(new THREE.Quaternion(), 0.1)
    }

    // ── 缩放：莫比乌斯态方块略小，更精致 ─────────────────────────────────
    const phase = dist * 0.15
    const localBreath = Math.sin(time * breathSpeed - phase) * 0.5 + 0.5
    const mobiusScale = 0.7  // 莫比乌斯态方块缩小
    const cubeScale = 1.0
    const baseScale = cubeScale + (mobiusScale - cubeScale) * morphT
    mesh.scale.setScalar(baseScale * (0.88 + localBreath * 0.12 + freqVal * 0.35 * audioActivity))

    // ── 颜色：蓝 ↔ 粉平滑切换 ─────────────────────────────────────────────
    const cubeColorT = proximity * 0.5 + localBreath * breathAmp * (1 - morphT)
    const flowColorT = (Math.sin(blockUBase[i] + flowOffset * 2) * 0.5 + 0.5) * 0.65 + freqVal * 0.35
    const t = Math.min(1, cubeColorT * (1 - morphT) + flowColorT * morphT + bassEnergy * 0.15)

    const color = gradientColor(t, colorBlend)
    const mat = mesh.material as THREE.MeshPhongMaterial
    mat.color.copy(color)
    mat.emissive.copy(color).multiplyScalar(0.12 + proximity * 0.3 + freqVal * 0.28 * audioActivity)
    mat.opacity = 0.5 + proximity * 0.35 + localBreath * 0.1
  })
}
