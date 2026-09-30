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
    --yaw -90          머리를 위 축 기준으로 돌린다. Tripo 머리와 몸통이 보는 방향이
                       다르면 옆을 보고 붙는다
    --out-scale 0.7    완성본 전체에 배율을 건다. 유니티에서 매번 손으로 줄이지 않도록
                       씬에서 쓰는 크기 그대로 내보낼 때
    --head-share 0.13  목 굵기 대신 머리 높이로 배율을 정한다. 몸통 키 대비 비율이며
                       사람은 보통 0.10~0.13 이다. 머리 없이 만든 몸통에서는 옷깃
                       구멍이 목보다 좁아 굵기로 맞추면 머리가 작아진다 — 그럴 때 쓴다
"""
import sys

import math

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

BONE = "Head"
FROM = "NeckTwist01"
EXTRA = 1.0
LIFT = 0.0
SHARE = 0.0         # 0 이 아니면 머리 높이를 몸통 키의 이 비율로 맞춘다 (목 굵기 대신)
YAW = 0.0           # 머리를 위 축 기준으로 몇 도 돌릴지. 머리와 몸통이 보는 방향이 다를 때
OUT_SCALE = 1.0     # 완성본 전체에 걸 배율. 씬에서 쓰는 크기에 맞춰 내보낼 때
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
    """월드 높이 z 위쪽을 잘라 낸다. 평면에서 실제로 쪼개므로 단면이 평평하다.

    평면을 오브젝트 로컬 좌표로 제대로 옮겨야 한다. 평행이동만 빼면 안 된다 —
    glTF 임포트는 Y-up 을 Z-up 으로 바꾸느라 오브젝트에 회전을 걸어 두는 일이 있고,
    그러면 자르는 면이 엉뚱한 곳에 놓여 몸통이 통째로 사라진다(실제로 그랬다)."""
    inverse = obj.matrix_world.inverted()
    plane_co = inverse @ Vector((0.0, 0.0, z_world))
    plane_no = (inverse.to_3x3().transposed() @ Vector((0.0, 0.0, 1.0))).normalized()
    mesh = bmesh.new()
    mesh.from_mesh(obj.data)
    bmesh.ops.bisect_plane(
        mesh, geom=list(mesh.verts) + list(mesh.edges) + list(mesh.faces), dist=1e-6,
        plane_co=plane_co, plane_no=plane_no,
        clear_inner=False, clear_outer=True)
    mesh.to_mesh(obj.data)
    mesh.free()
    obj.data.update()


def main():
    global BONE, FROM, EXTRA, LIFT, SHARE, YAW, OUT_SCALE
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
    SHARE = take("--head-share", float, SHARE)
    YAW = take("--yaw", float, YAW)
    OUT_SCALE = take("--out-scale", float, OUT_SCALE)

    bpy.ops.wm.read_factory_settings(use_empty=True)

    bpy.ops.import_scene.gltf(filepath=body_path)
    armature = next((o for o in bpy.context.scene.objects if o.type == "ARMATURE"), None)
    # 스킨이 걸린 메시만 몸통으로 본다. 작업 중 남은 잔재(이 프로젝트의 몸통에는
    # 반지름 1 짜리 Icosphere 가 하나 섞여 있었다)를 같이 재면 키가 틀어지고,
    # 키가 틀어지면 머리 크기가 통째로 어긋난다.
    body_meshes = [o for o in bpy.context.scene.objects
                   if o.type == "MESH" and len(o.vertex_groups) > 0]
    # 스킨이 걸리지 않은 메시는 키를 잴 때만 뺀다. 지우지는 않는다 — 뼈에 매달린
    # 소품(안경 같은 것)은 정점 그룹이 없어도 몸통의 일부다.
    ignored = [o.name for o in bpy.context.scene.objects
               if o.type == "MESH" and not o.vertex_groups]
    if ignored:
        print("[merge] 키 계산에서 제외(스킨 없음):", ", ".join(ignored))
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
    if SHARE > 0:
        # 머리 높이(목 위로 보이는 부분)를 몸통 키의 정해진 비율에 맞춘다.
        head_top = max(max(v.co.z for v in o.data.vertices) for o in head_meshes)
        visible = head_top - head_neck.z
        if visible <= 0:
            raise SystemExit("머리 목 위쪽 높이를 구하지 못했습니다")
        factor = (body_h * SHARE / visible) * EXTRA
        print(f"[merge] 머리 목: z={head_neck.z:.4f} · 목 위 높이 {visible:.4f} "
              f"· 목표 비율 {SHARE:.3f} · 배율 {factor:.4f}")
    else:
        factor = (body_radius / head_radius) * EXTRA
        print(f"[merge] 머리 목: z={head_neck.z:.4f} 반지름 {head_radius:.4f} "
              f"· 배율 {factor:.4f}")

    # 크기 → 회전 → 이동을 한 행렬로 건다. 나눠서 걸면 회전이 목 위치를 옮겨 버려
    # 이동량을 다시 구해야 한다. 회전한 목 위치를 그대로 써서 옮기면 어긋나지 않는다.
    turn = Matrix.Rotation(math.radians(YAW), 4, "Z")
    scale = Matrix.Scale(factor, 4)
    offset = body_centre - (turn @ (head_neck * factor)) + Vector((0, 0, body_h * LIFT))
    place = Matrix.Translation(offset) @ turn @ scale
    for obj in head_meshes:
        obj.matrix_world = place @ obj.matrix_world
    bpy.context.view_layer.update()
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    if YAW:
        print(f"[merge] 머리를 위 축 기준 {YAW:+.0f}° 돌렸다")

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

    if abs(OUT_SCALE - 1.0) > 1e-6:
        # 최상위에 빈 오브젝트를 하나 두고 거기에만 배율을 건다. 스킨이 걸린 메시와
        # 아마추어에 직접 걸어 적용하면 바인드가 틀어질 수 있다. glTF 로 나가면
        # 루트 노드의 스케일로 실려서 유니티가 그대로 읽는다.
        holder = bpy.data.objects.new("scale_root", None)
        bpy.context.scene.collection.objects.link(holder)
        for obj in list(bpy.context.scene.objects):
            if obj is not holder and obj.parent is None:
                obj.parent = holder
        holder.scale = (OUT_SCALE, OUT_SCALE, OUT_SCALE)
        bpy.context.view_layer.update()
        print(f"[merge] 완성본 전체에 배율 {OUT_SCALE} 를 걸었다")

    # 내보내기 직전에 무엇이 실려 나가는지 적는다. 잔재 메시가 섞여 유니티까지
    # 간 적이 있어, 결과물에 무엇이 들어 있는지는 눈에 보여야 한다.
    for obj in bpy.context.scene.objects:
        if obj.type == "MESH":
            print(f"[merge] 내보낼 메시: {obj.name} · 정점 {len(obj.data.vertices)}")

    bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", export_yup=True,
                              export_animations=True, export_skins=True)
    print("[done]", dst)


main()
