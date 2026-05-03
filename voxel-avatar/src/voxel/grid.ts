import * as THREE from 'three'

export interface VoxelGrid {
  meshes: THREE.Mesh[]
  origins: THREE.Vector3[]   // 每个方块的原始位置
  group: THREE.Group
}

const GRID = 6
const SIZE = 2
const GAP = 3.5

export function createVoxelGrid(scene: THREE.Scene): VoxelGrid {
  const group = new THREE.Group()
  const meshes: THREE.Mesh[] = []
  const origins: THREE.Vector3[] = []
  const geo = new THREE.BoxGeometry(SIZE, SIZE, SIZE)
  const offset = ((GRID - 1) * GAP) / 2

  for (let x = 0; x < GRID; x++) {
    for (let y = 0; y < GRID; y++) {
      for (let z = 0; z < GRID; z++) {
        const mat = new THREE.MeshPhongMaterial({
          color: 0x003366,
          emissive: 0x001133,
          transparent: true,
          opacity: 0.85,
        })
        const mesh = new THREE.Mesh(geo, mat)
        const pos = new THREE.Vector3(x * GAP - offset, y * GAP - offset, z * GAP - offset)
        mesh.position.copy(pos)
        group.add(mesh)
        meshes.push(mesh)
        origins.push(pos.clone())
      }
    }
  }

  scene.add(group)
  return { meshes, origins, group }
}
