import * as THREE from 'three'

export function createScene(canvas: HTMLCanvasElement) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true })
  renderer.setSize(window.innerWidth, window.innerHeight)
  renderer.setPixelRatio(window.devicePixelRatio)
  renderer.setClearColor(0x000000, 0)  // 完全透明背景

  const scene = new THREE.Scene()

  const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.1, 1000)
  camera.position.set(0, 0, 60)

  // 极暗环境光，突出内部发光感
  const ambient = new THREE.AmbientLight(0x000a22, 2)
  scene.add(ambient)

  // 核心发光体（动态强度由 animator 控制）
  const coreLight = new THREE.PointLight(0x4488ff, 8, 120)
  coreLight.position.set(0, 0, 0)
  scene.add(coreLight)

  // 核心发光球
  const coreGeo = new THREE.SphereGeometry(1.2, 16, 16)
  const coreMat = new THREE.MeshBasicMaterial({ color: 0xaaddff })
  const coreMesh = new THREE.Mesh(coreGeo, coreMat)
  scene.add(coreMesh)

  window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight
    camera.updateProjectionMatrix()
    renderer.setSize(window.innerWidth, window.innerHeight)
  })

  return { renderer, scene, camera, coreLight, coreMesh }
}
