"""잘라 낸 머리를 미리 리깅된 몸통에 얹어 하나의 GLB 로 내보낸다 (Blender 배치).

왜 이렇게 하나
──────────────
사람마다 전신을 새로 만들면 T포즈 생성을 거쳐야 하고, 그 단계가 얼굴을 망친다.
generate_image(t_pose) 는 1024 전신을 내놓아 얼굴이 언제나 70x100px 남짓이 되므로
(실험 11건 전수) 사진 화질을 올려도 소용이 없다. 게다가 생성 모델이 사람을 다시
그리므로 신원이 거기서 샌다. T포즈가 필요했던 유일한 이유는 자동 리깅이었다
(팔 내린 사진은 animate_prerigcheck 가 거부 — 운영 실패 8건 중 6건).

몸통을 미리 한 번 만들어 리깅해 두면 사람마다 할 일은 머리뿐이고, 원본 사진이
image_to_model 로 직행한다. 얼굴 픽셀이 70x100 에서 420x550 으로 올라간다.

무엇을 하나
───────────
1. 몸통 목에서 가장 가는 높이를 찾아 그 위를 잘라 낸다(몸통의 머리를 버린다).
2. 머리를 몸통 목 굵기에 맞춰 키우고 두 단면이 맞닿게 옮긴다.
3. 머리를 Head 본에 통째로 묶는다(가중치 1). 목이 움직이면 머리가 따라간다.
4. 몸통의 스켈레톤과 앉기 애니메이션을 그대로 둔 채 GLB 로 내보낸다.

머리의 목 단면은 head_trim.py 가 파일에 적어 둔 값(neck_z/x/y/r)을 읽는다. 메시
바닥을 재면 안 된다 — 긴 머리 모델의 바닥은 목이 아니라 머리카락 끝이라, 처음에
그렇게 했다가 머리카락 굵기를 목으로 착각해 머리가 0.61배로 줄었다.

    blender --background --python Survey/blender/head_body_merge.py -- \
        <몸통.glb> <머리.glb> <출력.glb> [옵션]

    --bone Head        머리를 묶을 본 이름
    --from NeckTwist01 목을 찾기 시작할 본 (여기부터 --bone 까지 훑어 가장 가는 곳을 쓴다)
    --scale 1.0        목 굵기로 구한 배율에 곱할 값. 머리가 크거나 작으면 조정
    --lift 0.0         맞춘 뒤 머리를 위아래로 미세 조정 (몸통 키 대비)
"""
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Vector

BONE = "Head"
FROM = "NeckTwist01"
EXTRA = 1.0
LIFT = 0.0
STEPS = 12           # 목에서 가장 가는 높이를 찾을 때 몇 군데를 재는지


def ring(objects, z, span):
    """높이 z 근처 정점들로 중심과 평균 반지름을 잰다 (월드 좌표)."""
    points = []
    for obj in objects:
        matrix = obj.matrix_world
        points += [matrix @ v.co for v in obj.data.vertices
                   if abs((matrix @ v.co).z - z) < span]
    if len(points) < 12:
        return None, None
    xy = np.array([(p.x, p.y) for p in points], dtype=np.float64)
    centre = xy.mean(axis=0)
    return Vector((centre[0], centre[1], z)), float(np.hypot(*(xy - centre).T).mean())


def cut_above(obj, z_world):
    """월드 높이 z 위쪽을 잘라 낸다. 평면에서 실제로 쪼개므로 단면이 평평하다."""
    local_z = z_world - obj.matrix_world.translation.z
    mesh = bmesh.new()
    mesh.from_mesh(obj.data)
    bmesh.ops.bisect_plane(
        mesh, geom=list(mesh.verts) + list(mesh.edges) + list(mesh.faces), dist=1e-6,
        plane_co=Vector((0, 0, local_z)), plane_no=Vector((0, 0, 1)),
        clear_inner=False, clear_outer=True)
    mesh.to_mesh(obj.data)
    mesh.free()
    obj.data.update()


def main():
    global BONE, FROM, EXTRA, LIFT
    argv = sys.argv[sys.argv.index("--") + 1:]
    body_path, head_path, dst = argv[0], argv[1], argv[2]
    options = argv[3:]

    def take(name, cast, current):
        nonlocal options
        if name not in options:
            return current
        i = options.index(name)
        value = cast(options[i + 1])
        options = options[:i] + options[i + 2:]
        return value

    BONE = take("--bone", str, BONE)
    FROM = take("--from", str, FROM)
    EXTRA = take("--scale", float, EXTRA)
    LIFT = take("--lift", float, LIFT)

    bpy.ops.wm.read_factory_settings(use_empty=True)

    bpy.ops.import_scene.gltf(filepath=body_path)
    armature = next((o for o in bpy.context.scene.objects if o.type == "ARMATURE"), None)
    body_meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if armature is None or not body_meshes:
        raise SystemExit("몸통에 아마추어나 메시가 없습니다")
    bones = armature.data.bones
    if BONE not in bones or FROM not in bones:
        raise SystemExit("본이 없습니다. 있는 본: " + ", ".join(b.name for b in bones))
    print(f"[merge] 몸통 메시 {len(body_meshes)} · 본 {len(bones)} · 액션 {len(bpy.data.actions)}")

    zs = [(o.matrix_world @ v.co).z for o in body_meshes for v in o.data.vertices]
    body_lo, body_hi = min(zs), max(zs)
    body_h = body_hi - body_lo
    low = (armature.matrix_world @ bones[FROM].head_local).z
    high = (armature.matrix_world @ bones[BONE].head_local).z
    print(f"[merge] 몸통 높이 {body_lo:.4f}~{body_hi:.4f} · "
          f"{FROM} {low:.4f} · {BONE} {high:.4f}")

    # 목에서 가장 가는 높이를 찾는다. 본 위치를 그대로 쓰면 안 된다 — Tripo 의 Head 본은
    # 목이 아니라 귀 높이(전체의 92.7%)에 있어서, 거기서 자르면 턱 너비를 목으로 잰다.
    best = None
    for i in range(STEPS + 1):
        z = low + (high - low) * i / STEPS
        centre, radius = ring(body_meshes, z, body_h * 0.008)
        if radius is not None and (best is None or radius < best[1]):
            best = (centre, radius)
    if best is None:
        raise SystemExit("몸통 목 단면을 재지 못했습니다")
    body_centre, body_radius = best
    print(f"[merge] 몸통 목: z={body_centre.z:.4f} "
          f"({100 * (body_centre.z - body_lo) / body_h:.1f}%) 반지름 {body_radius:.4f}")

    for obj in body_meshes:
        cut_above(obj, body_centre.z)

    # 머리를 읽는다. 목 단면은 head_trim.py 가 적어 둔 값을 쓴다.
    before = set(bpy.context.scene.objects)
    bpy.ops.import_scene.gltf(filepath=head_path)
    head_meshes = [o for o in bpy.context.scene.objects
                   if o not in before and o.type == "MESH"]
    if not head_meshes:
        raise SystemExit("머리에 메시가 없습니다")
    bpy.ops.object.select_all(action="DESELECT")
    for obj in head_meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = head_meshes[0]
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    marked = next((o for o in head_meshes if "neck_r" in o.keys()), None)
    if marked is None:
        raise SystemExit("머리에 목 단면 기록(neck_r)이 없습니다. head_trim.py 로 먼저 자르세요")
    head_neck = Vector((marked["neck_x"], marked["neck_y"], marked["neck_z"]))
    head_radius = float(marked["neck_r"])
    factor = (body_radius / head_radius) * EXTRA
    print(f"[merge] 머리 목: z={head_neck.z:.4f} 반지름 {head_radius:.4f} · 배율 {factor:.4f}")

    for obj in head_meshes:
        obj.scale = (factor, factor, factor)
    bpy.context.view_layer.update()
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    offset = body_centre - head_neck * factor + Vector((0, 0, body_h * LIFT))
    for obj in head_meshes:
        obj.location = offset
    bpy.context.view_layer.update()
    bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)

    top = max(max((o.matrix_world @ v.co).z for v in o.data.vertices) for o in head_meshes)
    share = 100 * (top - body_centre.z) / body_h
    print(f"[merge] 얹은 뒤 머리 꼭대기 {top:.4f} · 목 위로 전체 키의 {share:.1f}%")
    if not 6 <= share <= 20:
        print("[merge] 경고: 사람 머리는 보통 키의 10~13% 다. --scale 로 조정하세요")

    # Head 본에 통째로 묶는다. 이음새는 옷깃 안에 묻히므로 가중치를 섞지 않고 강체로
    # 두는 편이 단순하고 갈라지지 않는다.
    for obj in head_meshes:
        group = obj.vertex_groups.new(name=BONE)
        group.add([v.index for v in obj.data.vertices], 1.0, "REPLACE")
        modifier = obj.modifiers.new(name="Armature", type="ARMATURE")
        modifier.object = armature
        obj.parent = armature
        obj.matrix_parent_inverse = armature.matrix_world.inverted()
        print(f"[merge] '{obj.name}' 정점 {len(obj.data.vertices)} 를 '{BONE}' 에 묶음")

    bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", export_yup=True,
                              export_animations=True, export_skins=True)
    print("[done]", dst)


main()
